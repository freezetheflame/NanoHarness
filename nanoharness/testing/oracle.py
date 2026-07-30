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


class ToolCallParameters(_Parameters):
    required: List[str] = Field(default_factory=list)
    forbidden: List[str] = Field(default_factory=list)
    min_counts: Dict[str, NonNegativeInt] = Field(default_factory=dict)
    max_counts: Dict[str, NonNegativeInt] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_constraints(self):
        overlap = set(self.required) & set(self.forbidden)
        if overlap:
            raise ValueError(f"tools cannot be required and forbidden: {sorted(overlap)}")
        for name in set(self.min_counts) & set(self.max_counts):
            if self.min_counts[name] > self.max_counts[name]:
                raise ValueError(f"minimum exceeds maximum for tool {name!r}")
        return self


class ComponentErrorParameters(_Parameters):
    allowed_components: List[Literal["model", "tool", "hook"]] = Field(
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
            raise OracleConfigurationError(f"Oracle kind {kind!r} is already registered")
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
            OracleKind.TOOL_CALLS.value,
            ToolCallParameters,
            _evaluate_tool_calls,
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


def _evaluate_tool_calls(spec, parameters, context, oracle_id):
    calls = [
        event.payload.get("name")
        for event in context.trace.events
        if event.event_type in {TraceEventType.TOOL_EXCHANGE, TraceEventType.TOOL_ERROR}
    ]
    counts = Counter(name for name in calls if name is not None)
    failures = []
    for name in parameters.required:
        if counts[name] == 0:
            failures.append(f"required tool {name!r} was not called")
    for name in parameters.forbidden:
        if counts[name] > 0:
            failures.append(f"forbidden tool {name!r} was called {counts[name]} time(s)")
    for name, minimum in parameters.min_counts.items():
        if counts[name] < minimum:
            failures.append(f"tool {name!r} count {counts[name]} is below {minimum}")
    for name, maximum in parameters.max_counts.items():
        if counts[name] > maximum:
            failures.append(f"tool {name!r} count {counts[name]} exceeds {maximum}")
    return _verdict(
        spec,
        oracle_id,
        not failures,
        "; ".join(failures) if failures else "Tool-call constraints satisfied",
        {"counts": dict(counts)},
    )


def _evaluate_component_errors(spec, parameters, context, oracle_id):
    type_to_component = {
        TraceEventType.MODEL_ERROR: "model",
        TraceEventType.TOOL_ERROR: "tool",
        TraceEventType.HOOK_FAILED: "hook",
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
        "No disallowed component errors" if not errors else f"Found {len(errors)} error(s)",
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
        f"Expected execution error {expected}, got {error.model_dump() if error else None}",
        {"expected": expected, "actual": error.model_dump() if error else None},
    )
