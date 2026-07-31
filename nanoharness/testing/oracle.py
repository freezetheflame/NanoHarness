"""Deterministic oracle registry and built-in agent testing invariants."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Dict, List, Literal, Optional, Tuple, Type

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from nanoharness.core.schema import RunStatus, StopReason
from nanoharness.testing.scenario import (
    ExecutionError,
    OracleKind,
    OracleSeverity,
    OracleSpec,
    OracleVerdict,
    Scenario,
)
from nanoharness.testing.trace import AgentTrace, TraceEventType


class OracleConfigurationError(ValueError):
    """Raised before execution when a scenario contains an invalid oracle."""


class _Parameters(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GoalAchievementParameters(_Parameters):
    expected: bool = True


class RunStatusParameters(_Parameters):
    allowed: List[RunStatus] = Field(min_length=1)


class StopReasonParameters(_Parameters):
    allowed: List[StopReason] = Field(min_length=1)


class LifecycleParameters(_Parameters):
    require_single_task: bool = True


NonNegativeInt = Annotated[int, Field(ge=0)]


class ModelMessageParameters(_Parameters):
    require_scenario_query: bool = True
    every_exchange: bool = True
    min_user_messages: NonNegativeInt = 1


class ToolCallParameters(_Parameters):
    required: List[str] = Field(default_factory=list)
    forbidden: List[str] = Field(default_factory=list)
    min_counts: Dict[str, NonNegativeInt] = Field(default_factory=dict)
    max_counts: Dict[str, NonNegativeInt] = Field(default_factory=dict)
    required_arguments: Dict[str, List[str]] = Field(default_factory=dict)
    expected_arguments: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_constraints(self):
        overlap = set(self.required) & set(self.forbidden)
        if overlap:
            raise ValueError(
                f"tools cannot be required and forbidden: {sorted(overlap)}"
            )
        for name in set(self.min_counts) & set(self.max_counts):
            if self.min_counts[name] > self.max_counts[name]:
                raise ValueError(f"minimum exceeds maximum for tool {name!r}")
        return self


class ToolResultParameters(_Parameters):
    expected_last: Dict[str, Any] = Field(default_factory=dict)


class PermissionEnforcementParameters(_Parameters):
    denied_must_not_execute: bool = True
    require_decision_for_execution: bool = False


class StateValueParameters(_Parameters):
    min_saves: NonNegativeInt = 1
    required_keys: List[str] = Field(default_factory=list)
    expected_last: Dict[str, Any] = Field(default_factory=dict)


class StateDeltaParameters(_Parameters):
    scope: str = Field(min_length=1)
    expected_changes: Dict[str, Any] = Field(default_factory=dict)
    allow_unexpected_changes: bool = False


class SideEffectParameters(_Parameters):
    expected_attempts: Dict[str, NonNegativeInt] = Field(default_factory=dict)
    expected_commits: Dict[str, NonNegativeInt] = Field(default_factory=dict)
    expected_attributes: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    allow_unexpected_effects: bool = False
    require_unique_attempt_ids: bool = True


class ComponentErrorParameters(_Parameters):
    allowed_components: List[
        Literal["model", "tool", "context", "state", "hook", "permission"]
    ] = Field(
        default_factory=list
    )


class ExecutionErrorParameters(_Parameters):
    should_error: bool = True
    error_type: Optional[str] = None
    message_contains: Optional[str] = None

    @model_validator(mode="after")
    def validate_expectation(self):
        if not self.should_error and (
            self.error_type is not None or self.message_contains is not None
        ):
            raise ValueError(
                "error_type and message_contains require should_error=true"
            )
        return self


@dataclass(frozen=True)
class OracleContext:
    scenario: Scenario
    result: Any
    trace: AgentTrace
    execution_error: Optional[ExecutionError]


OracleFunction = Callable[[OracleSpec, BaseModel, OracleContext, str], OracleVerdict]


class OracleEvaluator:
    """Validates and evaluates serializable oracle specifications."""

    def __init__(self):
        self._registry: Dict[
            str,
            Tuple[Type[BaseModel], OracleFunction],
        ] = {}
        self._register_builtins()

    def register(
        self,
        kind: str,
        parameter_model: Type[BaseModel],
        evaluator: OracleFunction,
        *,
        replace: bool = False,
    ) -> None:
        if kind in self._registry and not replace:
            raise OracleConfigurationError(
                f"Oracle kind {kind!r} is already registered"
            )
        self._registry[kind] = (parameter_model, evaluator)

    def validate_specs(self, scenario: Scenario) -> None:
        for index, spec in enumerate(scenario.oracles):
            self._validated_parameters(spec, index)

    def evaluate(
        self,
        scenario: Scenario,
        *,
        result: Any,
        trace: AgentTrace,
        execution_error: Optional[ExecutionError],
    ) -> List[OracleVerdict]:
        context = OracleContext(
            scenario=scenario,
            result=result,
            trace=trace,
            execution_error=execution_error,
        )
        verdicts = []
        has_execution_error_oracle = False
        for index, spec in enumerate(scenario.oracles):
            parameters, evaluator = self._validated_parameters(spec, index)
            oracle_id = spec.oracle_id or f"{spec.kind}:{index}"
            verdicts.append(evaluator(spec, parameters, context, oracle_id))
            has_execution_error_oracle |= spec.kind == OracleKind.EXECUTION_ERROR.value

        if execution_error is not None and not has_execution_error_oracle:
            verdicts.append(
                OracleVerdict(
                    oracle_id="implicit:unexpected_execution_error",
                    kind=OracleKind.EXECUTION_ERROR.value,
                    passed=False,
                    message=(
                        f"Unexpected {execution_error.error_type}: "
                        f"{execution_error.message}"
                    ),
                    evidence=execution_error.model_dump(),
                )
            )
        return verdicts

    def _validated_parameters(
        self,
        spec: OracleSpec,
        index: int,
    ) -> Tuple[BaseModel, OracleFunction]:
        registration = self._registry.get(spec.kind)
        if registration is None:
            raise OracleConfigurationError(
                f"Unknown oracle kind {spec.kind!r} at index {index}"
            )
        parameter_model, evaluator = registration
        try:
            return parameter_model.model_validate(spec.parameters), evaluator
        except ValidationError as exc:
            raise OracleConfigurationError(
                f"Invalid parameters for oracle {spec.kind!r} at index {index}: {exc}"
            ) from exc

    def _register_builtins(self) -> None:
        self.register(
            OracleKind.GOAL_ACHIEVEMENT.value,
            GoalAchievementParameters,
            _evaluate_goal_achievement,
        )
        self.register(
            OracleKind.RUN_STATUS.value,
            RunStatusParameters,
            _evaluate_run_status,
        )
        self.register(
            OracleKind.STOP_REASON.value,
            StopReasonParameters,
            _evaluate_stop_reason,
        )
        self.register(
            OracleKind.LIFECYCLE.value,
            LifecycleParameters,
            _evaluate_lifecycle,
        )
        self.register(
            OracleKind.MODEL_MESSAGES.value,
            ModelMessageParameters,
            _evaluate_model_messages,
        )
        self.register(
            OracleKind.TOOL_CALLS.value,
            ToolCallParameters,
            _evaluate_tool_calls,
        )
        self.register(
            OracleKind.TOOL_RESULTS.value,
            ToolResultParameters,
            _evaluate_tool_results,
        )
        self.register(
            OracleKind.PERMISSION_ENFORCEMENT.value,
            PermissionEnforcementParameters,
            _evaluate_permission_enforcement,
        )
        self.register(
            OracleKind.STATE_VALUES.value,
            StateValueParameters,
            _evaluate_state_values,
        )
        self.register(
            OracleKind.STATE_DELTA.value,
            StateDeltaParameters,
            _evaluate_state_delta,
        )
        self.register(
            OracleKind.SIDE_EFFECTS.value,
            SideEffectParameters,
            _evaluate_side_effects,
        )
        self.register(
            OracleKind.COMPONENT_ERRORS.value,
            ComponentErrorParameters,
            _evaluate_component_errors,
        )
        self.register(
            OracleKind.EXECUTION_ERROR.value,
            ExecutionErrorParameters,
            _evaluate_execution_error,
        )


def _verdict(
    spec: OracleSpec,
    oracle_id: str,
    passed: bool,
    message: str,
    evidence: Optional[Dict[str, Any]] = None,
) -> OracleVerdict:
    return OracleVerdict(
        oracle_id=oracle_id,
        kind=spec.kind,
        passed=passed,
        message=message,
        severity=spec.severity,
        evidence=evidence or {},
    )


def _evaluate_goal_achievement(spec, parameters, context, oracle_id):
    actual = context.result.evaluation.achieved if context.result is not None else None
    passed = actual is parameters.expected
    return _verdict(
        spec,
        oracle_id,
        passed,
        f"Expected goal achievement {parameters.expected}, got {actual}",
        {"expected": parameters.expected, "actual": actual},
    )


def _evaluate_run_status(spec, parameters, context, oracle_id):
    actual = context.result.status if context.result is not None else None
    passed = actual in parameters.allowed if actual is not None else False
    return _verdict(
        spec,
        oracle_id,
        passed,
        f"Expected run status in {[item.value for item in parameters.allowed]}, "
        f"got {actual.value if actual is not None else None}",
        {
            "allowed": [item.value for item in parameters.allowed],
            "actual": actual.value if actual is not None else None,
        },
    )


def _evaluate_stop_reason(spec, parameters, context, oracle_id):
    actual = context.result.stop_reason if context.result is not None else None
    passed = actual in parameters.allowed if actual is not None else False
    return _verdict(
        spec,
        oracle_id,
        passed,
        f"Expected stop reason in {[item.value for item in parameters.allowed]}, "
        f"got {actual.value if actual is not None else None}",
        {
            "allowed": [item.value for item in parameters.allowed],
            "actual": actual.value if actual is not None else None,
        },
    )


def _evaluate_lifecycle(spec, parameters, context, oracle_id):
    starts = [
        event for event in context.trace.events
        if event.event_type is TraceEventType.TASK_STARTED
    ]
    ends = [
        event for event in context.trace.events
        if event.event_type is TraceEventType.TASK_COMPLETED
    ]
    timeline = [
        event.event_type
        for event in context.trace.events
        if event.event_type in {
            TraceEventType.TASK_STARTED,
            TraceEventType.TASK_COMPLETED,
        }
    ]
    expected_timeline = [
        item
        for _ in starts
        for item in (TraceEventType.TASK_STARTED, TraceEventType.TASK_COMPLETED)
    ]
    if parameters.require_single_task:
        passed = len(starts) == 1 and len(ends) == 1
    else:
        passed = len(starts) == len(ends) and bool(starts)
    passed = passed and timeline == expected_timeline
    return _verdict(
        spec,
        oracle_id,
        passed,
        f"Lifecycle contains {len(starts)} start event(s) and {len(ends)} end event(s)",
        {
            "start_sequences": [event.sequence for event in starts],
            "end_sequences": [event.sequence for event in ends],
        },
    )


def _evaluate_model_messages(spec, parameters, context, oracle_id):
    exchanges = [
        event
        for event in context.trace.events
        if event.event_type is TraceEventType.MODEL_EXCHANGE
    ]
    failures = []
    evidence = []
    for event in exchanges:
        messages = event.payload.get("messages") or []
        user_messages = [
            message
            for message in messages
            if isinstance(message, dict) and message.get("role") == "user"
        ]
        contains_query = any(
            context.scenario.query in str(message.get("content", ""))
            for message in user_messages
        )
        event_ok = len(user_messages) >= parameters.min_user_messages
        if parameters.require_scenario_query:
            event_ok = event_ok and contains_query
        evidence.append(
            {
                "sequence": event.sequence,
                "user_message_count": len(user_messages),
                "contains_scenario_query": contains_query,
                "passed": event_ok,
            }
        )
        if not event_ok:
            failures.append(event.sequence)
    if not exchanges:
        passed = False
    elif parameters.every_exchange:
        passed = not failures
    else:
        passed = len(failures) < len(exchanges)
    message = (
        "Model-message constraints satisfied"
        if passed
        else f"Model-message constraints failed at sequences {failures}"
    )
    return _verdict(
        spec,
        oracle_id,
        passed,
        message,
        {"exchanges": evidence},
    )


def _evaluate_tool_calls(spec, parameters, context, oracle_id):
    call_events = [
        event
        for event in context.trace.events
        if event.event_type in {TraceEventType.TOOL_EXCHANGE, TraceEventType.TOOL_ERROR}
    ]
    counts = Counter()
    for event in call_events:
        name = event.payload.get("name")
        if name is None:
            continue
        attempt_count = event.payload.get("attempt_count", 1)
        if (
            isinstance(attempt_count, bool)
            or not isinstance(attempt_count, int)
            or attempt_count < 1
        ):
            attempt_count = 1
        counts[name] += attempt_count
    failures = []
    for name in parameters.required:
        if counts[name] == 0:
            failures.append(f"required tool {name!r} was not called")
    for name in parameters.forbidden:
        if counts[name] > 0:
            failures.append(
                f"forbidden tool {name!r} was called {counts[name]} time(s)"
            )
    for name, minimum in parameters.min_counts.items():
        if counts[name] < minimum:
            failures.append(f"tool {name!r} count {counts[name]} is below {minimum}")
    for name, maximum in parameters.max_counts.items():
        if counts[name] > maximum:
            failures.append(f"tool {name!r} count {counts[name]} exceeds {maximum}")
    for name, required_arguments in parameters.required_arguments.items():
        matching = [event for event in call_events if event.payload.get("name") == name]
        if not matching:
            failures.append(f"tool {name!r} has no calls for argument validation")
        for event in matching:
            arguments = event.payload.get("arguments") or {}
            missing = [item for item in required_arguments if item not in arguments]
            if missing:
                failures.append(
                    f"tool {name!r} call at sequence {event.sequence} misses {missing}"
                )
    for name, expected_arguments in parameters.expected_arguments.items():
        matching = [event for event in call_events if event.payload.get("name") == name]
        if not matching:
            failures.append(f"tool {name!r} has no calls for argument comparison")
        for event in matching:
            arguments = event.payload.get("arguments") or {}
            for argument, expected in expected_arguments.items():
                actual = arguments.get(argument)
                if actual != expected:
                    failures.append(
                        f"tool {name!r} argument {argument!r} expected "
                        f"{expected!r}, got {actual!r}"
                    )
    return _verdict(
        spec,
        oracle_id,
        not failures,
        "; ".join(failures) if failures else "Tool-call constraints satisfied",
        {
            "counts": dict(counts),
            "calls": [
                {
                    "sequence": event.sequence,
                    "name": event.payload.get("name"),
                    "arguments": event.payload.get("arguments"),
                    "attempt_count": event.payload.get("attempt_count", 1),
                }
                for event in call_events
            ],
        },
    )


def _evaluate_tool_results(spec, parameters, context, oracle_id):
    results = {}
    for event in context.trace.events:
        if event.event_type is TraceEventType.TOOL_EXCHANGE:
            name = event.payload.get("name")
            if name is not None:
                results[name] = event.payload.get("result")
    failures = []
    for name, expected in parameters.expected_last.items():
        if name not in results:
            failures.append(f"tool {name!r} has no successful result")
        elif results[name] != expected:
            failures.append(
                f"tool {name!r} result expected {expected!r}, got {results[name]!r}"
            )
    return _verdict(
        spec,
        oracle_id,
        not failures,
        "; ".join(failures) if failures else "Tool-result constraints satisfied",
        {"last_results": results},
    )


def _evaluate_permission_enforcement(spec, parameters, context, oracle_id):
    decisions = [
        event
        for event in context.trace.events
        if event.event_type is TraceEventType.PERMISSION_DECISION
    ]
    executions = [
        event
        for event in context.trace.events
        if event.event_type in {TraceEventType.TOOL_EXCHANGE, TraceEventType.TOOL_ERROR}
    ]
    violations = []
    execution_evidence = []
    for execution in executions:
        tool_name = execution.payload.get("name")
        preceding = [
            decision
            for decision in decisions
            if decision.sequence < execution.sequence
            and decision.payload.get("tool_name") == tool_name
        ]
        latest = preceding[-1] if preceding else None
        decision_allowed = latest.payload.get("allowed") if latest else None
        execution_evidence.append(
            {
                "sequence": execution.sequence,
                "tool_name": tool_name,
                "decision_sequence": latest.sequence if latest else None,
                "decision_allowed": decision_allowed,
            }
        )
        if latest is None and parameters.require_decision_for_execution:
            violations.append(
                f"tool {tool_name!r} executed without a permission decision"
            )
        elif (
            latest is not None
            and parameters.denied_must_not_execute
            and decision_allowed is False
        ):
            violations.append(
                f"tool {tool_name!r} executed after denial at sequence "
                f"{latest.sequence}"
            )
    return _verdict(
        spec,
        oracle_id,
        not violations,
        (
            "Permission enforcement constraints satisfied"
            if not violations
            else "; ".join(violations)
        ),
        {
            "decisions": [
                {
                    "sequence": event.sequence,
                    "tool_name": event.payload.get("tool_name"),
                    "allowed": event.payload.get("allowed"),
                }
                for event in decisions
            ],
            "executions": execution_evidence,
        },
    )


def _evaluate_state_values(spec, parameters, context, oracle_id):
    saves = [
        event
        for event in context.trace.events
        if event.event_type is TraceEventType.STATE_SAVED
    ]
    failures = []
    if len(saves) < parameters.min_saves:
        failures.append(
            f"state save count {len(saves)} below {parameters.min_saves}"
        )
    last_state = saves[-1].payload.get("state", {}) if saves else {}
    if not isinstance(last_state, dict):
        failures.append("last state save is not a mapping")
        last_state = {}
    missing = [key for key in parameters.required_keys if key not in last_state]
    if missing:
        failures.append(f"last state misses keys {missing}")
    for key, expected in parameters.expected_last.items():
        actual = last_state.get(key)
        if actual != expected:
            failures.append(
                f"state key {key!r} expected {expected!r}, got {actual!r}"
            )
    return _verdict(
        spec,
        oracle_id,
        not failures,
        "; ".join(failures) if failures else "State-value constraints satisfied",
        {
            "save_count": len(saves),
            "last_state": last_state,
            "save_sequences": [event.sequence for event in saves],
        },
    )


def _evaluate_state_delta(spec, parameters, context, oracle_id):
    deltas = [
        event
        for event in context.trace.events
        if event.event_type is TraceEventType.ENVIRONMENT_DELTA
        and event.payload.get("scope") == parameters.scope
    ]
    failures = []
    if len(deltas) != 1:
        failures.append(
            f"expected one environment delta for {parameters.scope!r}, "
            f"got {len(deltas)}"
        )
    changes = deltas[0].payload.get("changes", {}) if len(deltas) == 1 else {}
    if not isinstance(changes, dict):
        failures.append("environment delta changes are not a mapping")
        changes = {}
    for path, expected in parameters.expected_changes.items():
        if path not in changes:
            failures.append(f"expected state change {path!r} is missing")
        elif changes[path] != expected:
            failures.append(
                f"state change {path!r} expected {expected!r}, "
                f"got {changes[path]!r}"
            )
    unexpected = sorted(set(changes) - set(parameters.expected_changes))
    if unexpected and not parameters.allow_unexpected_changes:
        failures.append(f"unexpected state changes {unexpected}")
    return _verdict(
        spec,
        oracle_id,
        not failures,
        "; ".join(failures) if failures else "State-delta constraints satisfied",
        {
            "scope": parameters.scope,
            "changes": changes,
            "unexpected_changes": unexpected,
            "delta_sequences": [event.sequence for event in deltas],
        },
    )


def _evaluate_side_effects(spec, parameters, context, oracle_id):
    events = [
        event
        for event in context.trace.events
        if event.event_type is TraceEventType.SIDE_EFFECT
    ]
    attempt_counts = Counter()
    commit_counts = Counter()
    attempt_ids = Counter()
    malformed = []
    ledger = []
    for event in events:
        effect_id = event.payload.get("effect_id")
        attempt_id = event.payload.get("attempt_id")
        outcome = event.payload.get("outcome")
        attributes = event.payload.get("attributes", {})
        if (
            not isinstance(effect_id, str)
            or not effect_id
            or not isinstance(attempt_id, str)
            or not attempt_id
            or outcome not in {"committed", "rejected", "failed"}
            or not isinstance(attributes, dict)
        ):
            malformed.append(event.sequence)
            continue
        attempt_counts[effect_id] += 1
        attempt_ids[attempt_id] += 1
        if outcome == "committed":
            commit_counts[effect_id] += 1
        ledger.append(
            {
                "sequence": event.sequence,
                "effect_id": effect_id,
                "attempt_id": attempt_id,
                "outcome": outcome,
                "attributes": attributes,
            }
        )

    failures = []
    if malformed:
        failures.append(f"malformed side-effect events at sequences {malformed}")
    duplicate_attempt_ids = sorted(
        attempt_id for attempt_id, count in attempt_ids.items() if count > 1
    )
    if duplicate_attempt_ids and parameters.require_unique_attempt_ids:
        failures.append(f"duplicate side-effect attempt IDs {duplicate_attempt_ids}")
    for effect_id, expected in parameters.expected_attempts.items():
        actual = attempt_counts[effect_id]
        if actual != expected:
            failures.append(
                f"side effect {effect_id!r} expected {expected} attempt(s), "
                f"got {actual}"
            )
    for effect_id, expected in parameters.expected_commits.items():
        actual = commit_counts[effect_id]
        if actual != expected:
            failures.append(
                f"side effect {effect_id!r} expected {expected} commit(s), "
                f"got {actual}"
            )
    for effect_id, expected in parameters.expected_attributes.items():
        matching = [item for item in ledger if item["effect_id"] == effect_id]
        if not matching:
            failures.append(
                f"side effect {effect_id!r} has no attempts for attribute validation"
            )
        for item in matching:
            mismatches = {
                key: {"expected": value, "actual": item["attributes"].get(key)}
                for key, value in expected.items()
                if item["attributes"].get(key) != value
            }
            if mismatches:
                failures.append(
                    f"side effect {effect_id!r} attempt {item['attempt_id']!r} "
                    f"has attribute mismatches {mismatches}"
                )
    expected_ids = (
        set(parameters.expected_attempts)
        | set(parameters.expected_commits)
        | set(parameters.expected_attributes)
    )
    actual_ids = set(attempt_counts)
    unexpected = sorted(actual_ids - expected_ids)
    if unexpected and not parameters.allow_unexpected_effects:
        failures.append(f"unexpected side effects {unexpected}")
    return _verdict(
        spec,
        oracle_id,
        not failures,
        "; ".join(failures) if failures else "Side-effect constraints satisfied",
        {
            "attempt_counts": dict(attempt_counts),
            "commit_counts": dict(commit_counts),
            "duplicate_attempt_ids": duplicate_attempt_ids,
            "unexpected_effects": unexpected,
            "ledger": ledger,
        },
    )


def _evaluate_component_errors(spec, parameters, context, oracle_id):
    type_to_component = {
        TraceEventType.MODEL_ERROR: "model",
        TraceEventType.TOOL_ERROR: "tool",
        TraceEventType.CONTEXT_ERROR: "context",
        TraceEventType.STATE_ERROR: "state",
        TraceEventType.HOOK_FAILED: "hook",
        TraceEventType.PERMISSION_ERROR: "permission",
    }
    errors = [
        {
            "component": type_to_component[event.event_type],
            "sequence": event.sequence,
            "error_type": event.payload.get("error_type"),
        }
        for event in context.trace.events
        if event.event_type in type_to_component
        and type_to_component[event.event_type] not in parameters.allowed_components
    ]
    return _verdict(
        spec,
        oracle_id,
        not errors,
        (
            "No disallowed component errors"
            if not errors
            else f"Found {len(errors)} error(s)"
        ),
        {"errors": errors, "allowed_components": parameters.allowed_components},
    )


def _evaluate_execution_error(spec, parameters, context, oracle_id):
    error = context.execution_error
    if parameters.should_error:
        passed = error is not None
        if passed and parameters.error_type is not None:
            passed = error.error_type == parameters.error_type
        if passed and parameters.message_contains is not None:
            passed = parameters.message_contains in error.message
    else:
        passed = error is None
    expected = {
        "should_error": parameters.should_error,
        "error_type": parameters.error_type,
        "message_contains": parameters.message_contains,
    }
    return _verdict(
        spec,
        oracle_id,
        passed,
        (
            f"Expected execution error {expected}, got "
            f"{error.model_dump() if error else None}"
        ),
        {"expected": expected, "actual": error.model_dump() if error else None},
    )
