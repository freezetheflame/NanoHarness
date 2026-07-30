"""Declarative, deterministic fault injection at agent component boundaries."""

from __future__ import annotations

import copy
import threading
from collections.abc import Callable, Sequence
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, Field, model_validator

from nanoharness.core.base import BaseToolRegistry, LLMProtocol
from nanoharness.core.engine import NanoEngine
from nanoharness.core.schema import LLMResponse
from nanoharness.testing.mutation import MutationStatus
from nanoharness.testing.oracle import OracleEvaluator
from nanoharness.testing.runner import ScenarioRunner
from nanoharness.testing.scenario import Scenario, ScenarioReport
from nanoharness.testing.trace import (
    TraceRecorder,
    normalize_trace_value,
    redact_sensitive_fields,
)


FAULT_SCHEMA_VERSION = 1


class FaultComponent(str, Enum):
    """Runtime boundary at which a fault is injected."""

    MODEL = "model"
    TOOL = "tool"


class FaultAction(str, Enum):
    """Executable fault actions supported by the first injection layer."""

    RAISE_ERROR = "raise_error"
    MODEL_RESPONSE_DROP = "model_response_drop"
    TOOL_NAME_SWAP = "tool_name_swap"
    TOOL_ARGUMENT_DROP = "tool_argument_drop"
    TOOL_RESULT_STALE = "tool_result_stale"
    TOOL_CALL_DUPLICATE = "tool_call_duplicate"


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
        if self.action in model_actions and self.component is not FaultComponent.MODEL:
            raise ValueError(f"{self.action.value} requires the model component")
        if self.action in tool_actions and self.component is not FaultComponent.TOOL:
            raise ValueError(f"{self.action.value} requires the tool component")
        if self.action is FaultAction.TOOL_NAME_SWAP and not self.replacement_tool_name:
            raise ValueError("tool_name_swap requires replacement_tool_name")
        if self.action is FaultAction.TOOL_ARGUMENT_DROP:
            if not self.tool_name:
                raise ValueError("tool_argument_drop requires tool_name")
            if not self.argument:
                raise ValueError("tool_argument_drop requires argument")
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
        if plan.schema_version != FAULT_SCHEMA_VERSION:
            raise ValueError(
                f"Fault schema version {plan.schema_version} is unsupported; "
                f"expected {FAULT_SCHEMA_VERSION}"
            )
        self.plan = FaultPlan.model_validate(plan.model_dump())
        self._matches = {rule.rule_id: 0 for rule in self.plan.rules}
        self._boundary_counts = {
            FaultComponent.MODEL: 0,
            FaultComponent.TOOL: 0,
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
        effective: bool = True,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        application = FaultApplication(
            sequence=0,
            rule_id=rule.rule_id,
            component=rule.component,
            action=rule.action,
            boundary_index=boundary_index,
            tool_name=tool_name,
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
                FaultComponent.MODEL: 0,
                FaultComponent.TOOL: 0,
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

    def __init__(self, delegate: BaseToolRegistry, session: FaultSession):
        self._delegate = delegate
        self.session = session

    def get_tool_schemas(self) -> List[Dict]:
        return self._delegate.get_tool_schemas()

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
        result = self._delegate.call(name, mutated_args)
        for rule in duplicate_rules:
            self.session.record(
                rule,
                boundary_index,
                tool_name=name,
                details={"executions_attempted": 2},
            )
            result = self._delegate.call(name, copy.deepcopy(mutated_args))

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
        return result

    def reset(self) -> None:
        self._delegate.reset()
        self.session.reset()


FaultEngineFactory = Callable[
    [Scenario, TraceRecorder, Optional[FaultSession]],
    NanoEngine,
]


class FaultCampaignRunner:
    """Executes fresh baseline and fault-injected scenarios against Oracles.

    The engine factory receives ``None`` for the baseline and a fresh
    ``FaultSession`` for each plan. It must place fault decorators inside
    recording decorators when the normalized trace should contain mutated I/O.
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
            if plan.schema_version != FAULT_SCHEMA_VERSION
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
