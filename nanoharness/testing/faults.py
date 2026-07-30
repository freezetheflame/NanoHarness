"""Declarative, deterministic fault injection at agent component boundaries."""

from __future__ import annotations

import copy
import threading
import time
from collections.abc import Callable, Sequence
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, Field, model_validator

from nanoharness.core.base import (
    BaseContextManager,
    BaseHookManager,
    BaseStateStore,
    BaseToolRegistry,
    LLMProtocol,
)
from nanoharness.core.engine import NanoEngine
from nanoharness.core.schema import AgentMessage, LLMResponse
from nanoharness.testing.mutation import MutationStatus
from nanoharness.testing.oracle import OracleEvaluator
from nanoharness.testing.runner import ScenarioRunner
from nanoharness.testing.runtime import PermissionProtocol
from nanoharness.testing.scenario import Scenario, ScenarioReport
from nanoharness.testing.trace import (
    TraceEventType,
    TraceRecorder,
    normalize_trace_value,
    redact_sensitive_fields,
)


FAULT_SCHEMA_VERSION = 2
SUPPORTED_FAULT_SCHEMA_VERSIONS = frozenset({1, FAULT_SCHEMA_VERSION})


class FaultComponent(str, Enum):
    """Runtime boundary at which a fault is injected."""

    MODEL = "model"
    TOOL = "tool"
    CONTEXT = "context"
    STATE = "state"
    HOOK = "hook"
    PERMISSION = "permission"


class FaultAction(str, Enum):
    """Executable fault actions supported by the first injection layer."""

    RAISE_ERROR = "raise_error"
    MODEL_RESPONSE_DROP = "model_response_drop"
    TOOL_NAME_SWAP = "tool_name_swap"
    TOOL_ARGUMENT_DROP = "tool_argument_drop"
    TOOL_RESULT_STALE = "tool_result_stale"
    TOOL_CALL_DUPLICATE = "tool_call_duplicate"
    CONTEXT_MESSAGE_DROP = "context_message_drop"
    CHECKPOINT_CORRUPT = "checkpoint_corrupt"
    STATE_SAVE_DROP = "state_save_drop"
    HOOK_SKIP = "hook_skip"
    PERMISSION_BYPASS = "permission_bypass"


class FaultRule(BaseModel):
    """One deterministic fault triggered at a zero-based matching occurrence."""

    rule_id: str = Field(min_length=1)
    component: FaultComponent
    action: FaultAction
    occurrence: int = Field(default=0, ge=0)
    tool_name: Optional[str] = None
    argument: Optional[str] = None
    replacement: Any = None
    replacement_tool_name: Optional[str] = None
    message_role: Optional[str] = None
    state_key: Optional[str] = None
    hook_stage: Optional[str] = None
    message: str = "injected component failure"
    enabled: bool = True

    @model_validator(mode="after")
    def validate_action(self):
        self.replacement = normalize_trace_value(self.replacement)
        model_actions = {
            FaultAction.MODEL_RESPONSE_DROP,
            FaultAction.TOOL_NAME_SWAP,
        }
        tool_actions = {
            FaultAction.TOOL_ARGUMENT_DROP,
            FaultAction.TOOL_RESULT_STALE,
            FaultAction.TOOL_CALL_DUPLICATE,
        }
        boundary_actions = {
            FaultAction.CONTEXT_MESSAGE_DROP: FaultComponent.CONTEXT,
            FaultAction.CHECKPOINT_CORRUPT: FaultComponent.STATE,
            FaultAction.STATE_SAVE_DROP: FaultComponent.STATE,
            FaultAction.HOOK_SKIP: FaultComponent.HOOK,
            FaultAction.PERMISSION_BYPASS: FaultComponent.PERMISSION,
        }
        if self.action in model_actions and self.component is not FaultComponent.MODEL:
            raise ValueError(f"{self.action.value} requires the model component")
        if self.action in tool_actions and self.component is not FaultComponent.TOOL:
            raise ValueError(f"{self.action.value} requires the tool component")
        expected_component = boundary_actions.get(self.action)
        if expected_component is not None and self.component is not expected_component:
            raise ValueError(
                f"{self.action.value} requires the {expected_component.value} component"
            )
        if self.action is FaultAction.TOOL_NAME_SWAP and not self.replacement_tool_name:
            raise ValueError("tool_name_swap requires replacement_tool_name")
        if self.action is FaultAction.TOOL_ARGUMENT_DROP:
            if not self.tool_name:
                raise ValueError("tool_argument_drop requires tool_name")
            if not self.argument:
                raise ValueError("tool_argument_drop requires argument")
        if self.action is FaultAction.CHECKPOINT_CORRUPT and not self.state_key:
            raise ValueError("checkpoint_corrupt requires state_key")
        if self.action is FaultAction.HOOK_SKIP and not self.hook_stage:
            raise ValueError("hook_skip requires hook_stage")
        for field_name in ("message_role", "state_key", "hook_stage"):
            value = getattr(self, field_name)
            if value is not None and not value:
                raise ValueError(f"{field_name} must be a non-empty string")
        selector_fields = {
            "tool_name": self.tool_name,
            "argument": self.argument,
            "replacement_tool_name": self.replacement_tool_name,
            "message_role": self.message_role,
            "state_key": self.state_key,
            "hook_stage": self.hook_stage,
        }
        allowed_selectors = {
            FaultAction.MODEL_RESPONSE_DROP: set(),
            FaultAction.TOOL_NAME_SWAP: {"tool_name", "replacement_tool_name"},
            FaultAction.TOOL_ARGUMENT_DROP: {"tool_name", "argument"},
            FaultAction.TOOL_RESULT_STALE: {"tool_name"},
            FaultAction.TOOL_CALL_DUPLICATE: {"tool_name"},
            FaultAction.CONTEXT_MESSAGE_DROP: {"message_role"},
            FaultAction.CHECKPOINT_CORRUPT: {"state_key"},
            FaultAction.STATE_SAVE_DROP: {"state_key"},
            FaultAction.HOOK_SKIP: {"hook_stage"},
            FaultAction.PERMISSION_BYPASS: {"tool_name"},
            FaultAction.RAISE_ERROR: {
                FaultComponent.MODEL: set(),
                FaultComponent.TOOL: {"tool_name"},
                FaultComponent.CONTEXT: {"message_role"},
                FaultComponent.STATE: {"state_key"},
                FaultComponent.HOOK: {"hook_stage"},
                FaultComponent.PERMISSION: {"tool_name"},
            }[self.component],
        }[self.action]
        unrelated = sorted(
            name
            for name, value in selector_fields.items()
            if value is not None and name not in allowed_selectors
        )
        if unrelated:
            raise ValueError(
                f"{self.action.value} has unrelated selectors: {unrelated}"
            )
        return self


class FaultPlan(BaseModel):
    """Versioned collection of fault rules shared by runtime decorators."""

    schema_version: int = Field(default=FAULT_SCHEMA_VERSION, ge=1)
    plan_id: str = Field(min_length=1)
    rules: List[FaultRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_rule_ids(self):
        rule_ids = [rule.rule_id for rule in self.rules]
        duplicates = sorted(
            rule_id for rule_id in set(rule_ids) if rule_ids.count(rule_id) > 1
        )
        if duplicates:
            raise ValueError(f"Duplicate fault rule IDs: {duplicates}")
        return self


class FaultApplication(BaseModel):
    """Evidence that a configured rule was selected during execution."""

    sequence: int = Field(ge=0)
    rule_id: str
    component: FaultComponent
    action: FaultAction
    boundary_index: int = Field(ge=0)
    tool_name: Optional[str] = None
    message_role: Optional[str] = None
    state_key: Optional[str] = None
    hook_stage: Optional[str] = None
    effective: bool = True
    details: Dict[str, Any] = Field(default_factory=dict)


class FaultReport(BaseModel):
    """Serializable runtime evidence for one fault-injected execution."""

    schema_version: int = Field(default=FAULT_SCHEMA_VERSION, ge=1)
    plan_id: str
    configured_rules: List[FaultRule] = Field(default_factory=list)
    applications: List[FaultApplication] = Field(default_factory=list)

    @property
    def applied_rule_ids(self) -> List[str]:
        return [application.rule_id for application in self.applications]

    @property
    def effective_rule_ids(self) -> List[str]:
        return [
            application.rule_id
            for application in self.applications
            if application.effective
        ]

    @property
    def untriggered_rule_ids(self) -> List[str]:
        applied = set(self.applied_rule_ids)
        return [
            rule.rule_id
            for rule in self.configured_rules
            if rule.rule_id not in applied
        ]


class FaultCampaignOutcome(BaseModel):
    """Oracle classification and execution evidence for one fault plan."""

    plan_id: str
    status: MutationStatus
    message: str
    scenario_report: Optional[ScenarioReport] = None
    fault_report: Optional[FaultReport] = None
    error_type: Optional[str] = None


class FaultCampaignReport(BaseModel):
    """Baseline and executable fault outcomes for one scenario."""

    schema_version: int = Field(default=FAULT_SCHEMA_VERSION, ge=1)
    scenario_id: str
    baseline: ScenarioReport
    outcomes: List[FaultCampaignOutcome] = Field(default_factory=list)
    killed: int = 0
    survived: int = 0
    not_applicable: int = 0
    equivalent: int = 0
    errors: int = 0
    mutation_score: Optional[float] = None

    @model_validator(mode="after")
    def validate_accounting(self):
        if self.baseline.scenario_id != self.scenario_id:
            raise ValueError("Fault Campaign baseline Scenario does not match")
        plan_ids = [outcome.plan_id for outcome in self.outcomes]
        if len(set(plan_ids)) != len(plan_ids):
            raise ValueError("Fault Campaign outcome plan IDs must be unique")
        expected = {
            "killed": sum(
                item.status is MutationStatus.KILLED for item in self.outcomes
            ),
            "survived": sum(
                item.status is MutationStatus.SURVIVED for item in self.outcomes
            ),
            "not_applicable": sum(
                item.status is MutationStatus.NOT_APPLICABLE
                for item in self.outcomes
            ),
            "equivalent": sum(
                item.status is MutationStatus.EQUIVALENT for item in self.outcomes
            ),
            "errors": sum(
                item.status is MutationStatus.ERROR for item in self.outcomes
            ),
        }
        for field_name, value in expected.items():
            if getattr(self, field_name) != value:
                raise ValueError(
                    f"Fault Campaign {field_name} does not match outcomes"
                )
        denominator = expected["killed"] + expected["survived"]
        score = expected["killed"] / denominator if denominator else None
        if self.mutation_score != score:
            raise ValueError("Fault Campaign mutation_score does not match outcomes")
        return self


class FaultCampaignConfigurationError(ValueError):
    """Raised before baseline execution for an ambiguous fault campaign."""


class InjectedFaultError(RuntimeError):
    """Stable exception raised by a ``raise_error`` rule."""

    def __init__(self, rule_id: str, message: str):
        super().__init__(f"Injected fault {rule_id!r}: {message}")
        self.rule_id = rule_id
        self.injected_message = message


class FaultSession:
    """Thread-safe rule matcher and evidence collector for one execution."""

    def __init__(self, plan: FaultPlan):
        if plan.schema_version not in SUPPORTED_FAULT_SCHEMA_VERSIONS:
            raise ValueError(
                f"Fault schema version {plan.schema_version} is unsupported; "
                f"supported versions are {sorted(SUPPORTED_FAULT_SCHEMA_VERSIONS)}"
            )
        self.plan = FaultPlan.model_validate(plan.model_dump())
        self.plan.schema_version = FAULT_SCHEMA_VERSION
        self._matches = {rule.rule_id: 0 for rule in self.plan.rules}
        self._boundary_counts = {
            component: 0 for component in FaultComponent
        }
        self._applications: List[FaultApplication] = []
        self._lock = threading.RLock()

    def next_boundary(self, component: FaultComponent) -> int:
        with self._lock:
            index = self._boundary_counts[component]
            self._boundary_counts[component] += 1
            return index

    def select(
        self,
        component: FaultComponent,
        actions: Set[FaultAction],
        *,
        tool_name: Optional[str] = None,
        message_role: Optional[str] = None,
        state_keys: Optional[Set[str]] = None,
        hook_stage: Optional[str] = None,
    ) -> List[FaultRule]:
        """Select rules for this matching opportunity and advance their cursors."""

        selected = []
        with self._lock:
            for rule in self.plan.rules:
                if not rule.enabled or rule.component is not component:
                    continue
                if rule.action not in actions:
                    continue
                if rule.tool_name is not None and rule.tool_name != tool_name:
                    continue
                if rule.message_role is not None and rule.message_role != message_role:
                    continue
                if rule.state_key is not None and (
                    state_keys is None or rule.state_key not in state_keys
                ):
                    continue
                if rule.hook_stage is not None and rule.hook_stage != hook_stage:
                    continue
                match_index = self._matches[rule.rule_id]
                self._matches[rule.rule_id] += 1
                if match_index == rule.occurrence:
                    selected.append(FaultRule.model_validate(rule.model_dump()))
        return selected

    def record(
        self,
        rule: FaultRule,
        boundary_index: int,
        *,
        tool_name: Optional[str] = None,
        message_role: Optional[str] = None,
        state_key: Optional[str] = None,
        hook_stage: Optional[str] = None,
        effective: bool = True,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        application = FaultApplication(
            sequence=0,
            rule_id=rule.rule_id,
            component=rule.component,
            action=rule.action,
            boundary_index=boundary_index,
            tool_name=tool_name if tool_name is not None else rule.tool_name,
            message_role=(
                message_role if message_role is not None else rule.message_role
            ),
            state_key=state_key if state_key is not None else rule.state_key,
            hook_stage=hook_stage if hook_stage is not None else rule.hook_stage,
            effective=effective,
            details=redact_sensitive_fields(normalize_trace_value(details or {})),
        )
        with self._lock:
            application.sequence = len(self._applications)
            self._applications.append(application)

    def report(self) -> FaultReport:
        with self._lock:
            return FaultReport(
                plan_id=self.plan.plan_id,
                configured_rules=[
                    redact_sensitive_fields(normalize_trace_value(rule.model_dump()))
                    for rule in self.plan.rules
                ],
                applications=[
                    application.model_dump() for application in self._applications
                ],
            )

    def reset(self) -> None:
        with self._lock:
            self._matches = {rule.rule_id: 0 for rule in self.plan.rules}
            self._boundary_counts = {
                component: 0 for component in FaultComponent
            }
            self._applications = []


class FaultInjectingLLM:
    """LLM decorator that executes model-boundary rules from a fault plan."""

    def __init__(self, delegate: LLMProtocol, session: FaultSession):
        self._delegate = delegate
        self.session = session

    def chat(self, messages, tools=None) -> LLMResponse:
        boundary_index = self.session.next_boundary(FaultComponent.MODEL)
        error_rules = self.session.select(
            FaultComponent.MODEL,
            {FaultAction.RAISE_ERROR},
        )
        if error_rules:
            rule = error_rules[0]
            self.session.record(rule, boundary_index)
            for suppressed in error_rules[1:]:
                self.session.record(
                    suppressed,
                    boundary_index,
                    effective=False,
                    details={"suppressed_by": rule.rule_id},
                )
            raise InjectedFaultError(rule.rule_id, rule.message)

        response = LLMResponse.model_validate(
            self._delegate.chat(messages, tools=tools).model_dump()
        )
        for rule in self.session.select(
            FaultComponent.MODEL,
            {FaultAction.MODEL_RESPONSE_DROP},
        ):
            effective = bool(response.content or response.tool_calls)
            response = LLMResponse(content="", tool_calls=None)
            self.session.record(
                rule,
                boundary_index,
                effective=effective,
                details={"dropped_content_and_tool_calls": True},
            )

        if response.tool_calls:
            for call in response.tool_calls:
                original_name = call.name
                rules = self.session.select(
                    FaultComponent.MODEL,
                    {FaultAction.TOOL_NAME_SWAP},
                    tool_name=original_name,
                )
                for rule in rules:
                    call.name = str(rule.replacement_tool_name)
                    self.session.record(
                        rule,
                        boundary_index,
                        tool_name=original_name,
                        effective=original_name != call.name,
                        details={
                            "original_name": original_name,
                            "replacement_name": call.name,
                        },
                    )
        return response

    def reset(self) -> None:
        reset = getattr(self._delegate, "reset", None)
        if reset is not None:
            reset()
        self.session.reset()


class FaultInjectingToolRegistry(BaseToolRegistry):
    """Tool decorator that injects argument, result, duplication, and errors."""

    def __init__(
        self,
        delegate: BaseToolRegistry,
        session: FaultSession,
        *,
        recorder: Optional[TraceRecorder] = None,
        clock: Callable[[], float] = time.perf_counter,
    ):
        self._delegate = delegate
        self.session = session
        self._recorder = recorder
        self._clock = clock

    def get_tool_schemas(self) -> List[Dict]:
        schemas = self._delegate.get_tool_schemas()
        if self._recorder is not None:
            self._recorder.record(
                TraceEventType.TOOL_SCHEMAS,
                {"schemas": schemas},
            )
        return schemas

    def call(self, name: str, args: Dict) -> Any:
        boundary_index = self.session.next_boundary(FaultComponent.TOOL)
        error_rules = self.session.select(
            FaultComponent.TOOL,
            {FaultAction.RAISE_ERROR},
            tool_name=name,
        )
        if error_rules:
            rule = error_rules[0]
            self.session.record(rule, boundary_index, tool_name=name)
            for suppressed in error_rules[1:]:
                self.session.record(
                    suppressed,
                    boundary_index,
                    tool_name=name,
                    effective=False,
                    details={"suppressed_by": rule.rule_id},
                )
            raise InjectedFaultError(rule.rule_id, rule.message)

        mutated_args = copy.deepcopy(args)
        for rule in self.session.select(
            FaultComponent.TOOL,
            {FaultAction.TOOL_ARGUMENT_DROP},
            tool_name=name,
        ):
            effective = rule.argument in mutated_args
            if effective:
                del mutated_args[str(rule.argument)]
            self.session.record(
                rule,
                boundary_index,
                tool_name=name,
                effective=effective,
                details={"dropped_argument": rule.argument},
            )

        duplicate_rules = self.session.select(
            FaultComponent.TOOL,
            {FaultAction.TOOL_CALL_DUPLICATE},
            tool_name=name,
        )
        attempt_count = 1
        result = self._call_delegate(
            name,
            mutated_args,
            attempt_index=0,
        )
        for rule in duplicate_rules:
            self.session.record(
                rule,
                boundary_index,
                tool_name=name,
                details={"duplicate_attempt_number": attempt_count + 1},
            )
            result = self._call_delegate(
                name,
                copy.deepcopy(mutated_args),
                attempt_index=attempt_count,
            )
            attempt_count += 1

        for rule in self.session.select(
            FaultComponent.TOOL,
            {FaultAction.TOOL_RESULT_STALE},
            tool_name=name,
        ):
            effective = result != rule.replacement
            original_result = result
            result = copy.deepcopy(rule.replacement)
            self.session.record(
                rule,
                boundary_index,
                tool_name=name,
                effective=effective,
                details={
                    "original_result": original_result,
                    "replacement_result": result,
                },
            )
        if self._recorder is not None:
            self._recorder.record(
                TraceEventType.TOOL_EXCHANGE,
                {
                    "name": name,
                    "arguments": mutated_args,
                    "result": result,
                    "attempt_count": attempt_count,
                },
            )
        return result

    def reset(self) -> None:
        self._delegate.reset()
        self.session.reset()

    def _call_delegate(
        self,
        name: str,
        arguments: Dict,
        *,
        attempt_index: int,
    ) -> Any:
        if self._recorder is None:
            return self._delegate.call(name, arguments)
        started_at = self._clock()
        self._recorder.record(
            TraceEventType.TOOL_STARTED,
            {
                "name": name,
                "arguments": arguments,
                "attempt_index": attempt_index,
            },
        )
        try:
            result = self._delegate.call(name, arguments)
        except Exception as exc:
            self._recorder.record(
                TraceEventType.TOOL_ERROR,
                {
                    "name": name,
                    "arguments": arguments,
                    "attempt_index": attempt_index,
                    "attempt_count": attempt_index + 1,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "duration_ms": (self._clock() - started_at) * 1000,
                },
            )
            raise
        self._recorder.record(
            TraceEventType.TOOL_COMPLETED,
            {
                "name": name,
                "arguments": arguments,
                "result": result,
                "attempt_index": attempt_index,
                "duration_ms": (self._clock() - started_at) * 1000,
            },
        )
        return result


class FaultInjectingContextManager(BaseContextManager):
    """Context decorator supporting dropped messages and injected errors."""

    def __init__(self, delegate: BaseContextManager, session: FaultSession):
        self._delegate = delegate
        self.session = session

    def add_message(self, msg: AgentMessage):
        boundary_index = self.session.next_boundary(FaultComponent.CONTEXT)
        role = msg.role
        error_rules = self.session.select(
            FaultComponent.CONTEXT,
            {FaultAction.RAISE_ERROR},
            message_role=role,
        )
        _raise_selected_faults(
            self.session,
            error_rules,
            boundary_index,
            message_role=role,
        )
        drop_rules = self.session.select(
            FaultComponent.CONTEXT,
            {FaultAction.CONTEXT_MESSAGE_DROP},
            message_role=role,
        )
        if drop_rules:
            for rule in drop_rules:
                self.session.record(
                    rule,
                    boundary_index,
                    message_role=role,
                    details={"dropped_role": role},
                )
            return None
        return self._delegate.add_message(msg)

    def get_full_context(self):
        boundary_index = self.session.next_boundary(FaultComponent.CONTEXT)
        error_rules = self.session.select(
            FaultComponent.CONTEXT,
            {FaultAction.RAISE_ERROR},
        )
        _raise_selected_faults(self.session, error_rules, boundary_index)
        return self._delegate.get_full_context()

    def reset(self):
        self._delegate.reset()
        self.session.reset()


class FaultInjectingStateStore(BaseStateStore):
    """State decorator supporting dropped and corrupted checkpoint writes."""

    def __init__(self, delegate: BaseStateStore, session: FaultSession):
        self._delegate = delegate
        self.session = session

    def save_state(self, state: Dict):
        boundary_index = self.session.next_boundary(FaultComponent.STATE)
        state_keys = {key for key in state if isinstance(key, str)}
        error_rules = self.session.select(
            FaultComponent.STATE,
            {FaultAction.RAISE_ERROR},
            state_keys=state_keys,
        )
        _raise_selected_faults(self.session, error_rules, boundary_index)
        drop_rules = self.session.select(
            FaultComponent.STATE,
            {FaultAction.STATE_SAVE_DROP},
            state_keys=state_keys,
        )
        if drop_rules:
            for rule in drop_rules:
                self.session.record(
                    rule,
                    boundary_index,
                    state_key=rule.state_key,
                    details={"dropped_save": True},
                )
            return None
        mutated = copy.deepcopy(state)
        for rule in self.session.select(
            FaultComponent.STATE,
            {FaultAction.CHECKPOINT_CORRUPT},
            state_keys=state_keys,
        ):
            original = mutated.get(str(rule.state_key))
            mutated[str(rule.state_key)] = copy.deepcopy(rule.replacement)
            self.session.record(
                rule,
                boundary_index,
                state_key=rule.state_key,
                effective=original != rule.replacement,
                details={
                    "original_value": original,
                    "replacement_value": rule.replacement,
                },
            )
        return self._delegate.save_state(mutated)

    def load_state(self) -> Dict:
        boundary_index = self.session.next_boundary(FaultComponent.STATE)
        error_rules = self.session.select(
            FaultComponent.STATE,
            {FaultAction.RAISE_ERROR},
        )
        _raise_selected_faults(self.session, error_rules, boundary_index)
        return self._delegate.load_state()

    def reset(self):
        self._delegate.reset()
        self.session.reset()


class FaultInjectingHookManager(BaseHookManager):
    """Hook decorator supporting deterministic lifecycle-stage skipping."""

    def __init__(self, delegate: BaseHookManager, session: FaultSession):
        self._delegate = delegate
        self.session = session

    def register(self, stage: str, hook):
        return self._delegate.register(stage, hook)

    def trigger(self, stage: str, data: Any):
        stage_value = str(getattr(stage, "value", stage))
        boundary_index = self.session.next_boundary(FaultComponent.HOOK)
        error_rules = self.session.select(
            FaultComponent.HOOK,
            {FaultAction.RAISE_ERROR},
            hook_stage=stage_value,
        )
        _raise_selected_faults(
            self.session,
            error_rules,
            boundary_index,
            hook_stage=stage_value,
        )
        skip_rules = self.session.select(
            FaultComponent.HOOK,
            {FaultAction.HOOK_SKIP},
            hook_stage=stage_value,
        )
        if skip_rules:
            for rule in skip_rules:
                self.session.record(
                    rule,
                    boundary_index,
                    hook_stage=stage_value,
                    details={"skipped_stage": stage_value},
                )
            return None
        return self._delegate.trigger(stage, data)

    def reset(self):
        self._delegate.reset()
        self.session.reset()


class FaultInjectingPermissionManager:
    """Permission decorator supporting denied-call bypass and errors."""

    def __init__(self, delegate: PermissionProtocol, session: FaultSession):
        self._delegate = delegate
        self.session = session

    def enforce(self, tool_name: str, args: Dict) -> Optional[str]:
        boundary_index = self.session.next_boundary(FaultComponent.PERMISSION)
        error_rules = self.session.select(
            FaultComponent.PERMISSION,
            {FaultAction.RAISE_ERROR},
            tool_name=tool_name,
        )
        _raise_selected_faults(
            self.session,
            error_rules,
            boundary_index,
            tool_name=tool_name,
        )
        denial = self._delegate.enforce(tool_name, args)
        for rule in self.session.select(
            FaultComponent.PERMISSION,
            {FaultAction.PERMISSION_BYPASS},
            tool_name=tool_name,
        ):
            self.session.record(
                rule,
                boundary_index,
                tool_name=tool_name,
                effective=denial is not None,
                details={"original_denial": denial},
            )
            denial = None
        return denial

    def reset(self):
        reset = getattr(self._delegate, "reset", None)
        if reset is not None:
            reset()
        self.session.reset()


def _raise_selected_faults(
    session: FaultSession,
    rules: Sequence[FaultRule],
    boundary_index: int,
    **selectors: Any,
) -> None:
    if not rules:
        return
    selected = rules[0]
    session.record(selected, boundary_index, **selectors)
    for suppressed in rules[1:]:
        session.record(
            suppressed,
            boundary_index,
            effective=False,
            details={"suppressed_by": selected.rule_id},
            **selectors,
        )
    raise InjectedFaultError(selected.rule_id, selected.message)


FaultEngineFactory = Callable[
    [Scenario, TraceRecorder, Optional[FaultSession]],
    NanoEngine,
]


class FaultCampaignRunner:
    """Executes fresh baseline and fault-injected scenarios against Oracles.

    The engine factory receives ``None`` for the baseline and a fresh
    ``FaultSession`` for each plan. Place model/tool result-transforming faults
    inside recording decorators. Place Context/state/hook/permission
    suppression decorators outside recording so skipped calls are not falsely
    recorded as completed operations.
    """

    def __init__(
        self,
        engine_factory: FaultEngineFactory,
        *,
        oracle_evaluator: Optional[OracleEvaluator] = None,
    ):
        self._engine_factory = engine_factory
        self._oracles = oracle_evaluator or OracleEvaluator()

    def run(
        self,
        scenario: Scenario,
        plans: Sequence[FaultPlan],
    ) -> FaultCampaignReport:
        self._validate_plans(plans)
        baseline_runner = ScenarioRunner(
            lambda current, recorder: self._engine_factory(
                current,
                recorder,
                None,
            ),
            recorder_factory=lambda current: self._recorder(
                current,
                execution_mode="baseline",
            ),
            oracle_evaluator=self._oracles,
        )
        baseline = baseline_runner.run(scenario)
        outcomes = []
        for plan in plans:
            if not baseline.passed:
                outcomes.append(
                    FaultCampaignOutcome(
                        plan_id=plan.plan_id,
                        status=MutationStatus.BASELINE_FAILED,
                        message="Baseline scenario failed; fault plan was not executed",
                    )
                )
                continue
            session = FaultSession(plan)
            injected_runner = ScenarioRunner(
                lambda current, recorder, active=session: self._engine_factory(
                    current,
                    recorder,
                    active,
                ),
                oracle_evaluator=baseline_runner.oracle_evaluator,
                recorder_factory=lambda current, plan_id=plan.plan_id: self._recorder(
                    current,
                    execution_mode="fault_injected",
                    plan_id=plan_id,
                ),
            )
            scenario_report = injected_runner.run(scenario)
            fault_report = session.report()
            if (
                not fault_report.applications
                and scenario_report.execution_error is not None
            ):
                status = MutationStatus.ERROR
                message = "Fault-injected execution failed before any rule applied"
            elif not fault_report.applications:
                status = MutationStatus.NOT_APPLICABLE
                message = "No configured fault rule matched during execution"
            elif not fault_report.effective_rule_ids:
                status = MutationStatus.EQUIVALENT
                message = "Fault rules matched but did not alter an observed value"
            elif scenario_report.passed:
                status = MutationStatus.SURVIVED
                message = "Effective executable faults survived all Oracles"
            else:
                status = MutationStatus.KILLED
                message = "At least one Oracle detected the executable faults"
            outcomes.append(
                FaultCampaignOutcome(
                    plan_id=plan.plan_id,
                    status=status,
                    message=message,
                    scenario_report=scenario_report,
                    fault_report=fault_report,
                )
            )
        killed = sum(outcome.status is MutationStatus.KILLED for outcome in outcomes)
        survived = sum(
            outcome.status is MutationStatus.SURVIVED for outcome in outcomes
        )
        not_applicable = sum(
            outcome.status is MutationStatus.NOT_APPLICABLE for outcome in outcomes
        )
        equivalent = sum(
            outcome.status is MutationStatus.EQUIVALENT for outcome in outcomes
        )
        errors = sum(outcome.status is MutationStatus.ERROR for outcome in outcomes)
        denominator = killed + survived
        return FaultCampaignReport(
            scenario_id=scenario.scenario_id,
            baseline=baseline,
            outcomes=outcomes,
            killed=killed,
            survived=survived,
            not_applicable=not_applicable,
            equivalent=equivalent,
            errors=errors,
            mutation_score=killed / denominator if denominator else None,
        )

    @staticmethod
    def _validate_plans(plans: Sequence[FaultPlan]) -> None:
        plan_ids = [plan.plan_id for plan in plans]
        duplicates = sorted(
            plan_id for plan_id in set(plan_ids) if plan_ids.count(plan_id) > 1
        )
        if duplicates:
            raise FaultCampaignConfigurationError(
                f"Duplicate fault plan IDs: {duplicates}"
            )
        unsupported = sorted(
            plan.plan_id
            for plan in plans
            if plan.schema_version not in SUPPORTED_FAULT_SCHEMA_VERSIONS
        )
        if unsupported:
            raise FaultCampaignConfigurationError(
                f"Unsupported fault plan versions: {unsupported}"
            )

    @staticmethod
    def _recorder(
        scenario: Scenario,
        *,
        execution_mode: str,
        plan_id: Optional[str] = None,
    ) -> TraceRecorder:
        metadata = {
            "scenario_id": scenario.scenario_id,
            "seed": scenario.seed,
            "tags": scenario.tags,
            "execution_mode": execution_mode,
        }
        if plan_id is not None:
            metadata["fault_plan_id"] = plan_id
        return TraceRecorder(metadata=metadata)
