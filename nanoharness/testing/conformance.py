"""Versioned semantic projections for cross-runtime conformance evidence."""

from __future__ import annotations

from datetime import datetime
from collections.abc import Mapping, Sequence
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from nanoharness.testing.scenario import Scenario, ScenarioReport
from nanoharness.testing.trace import (
    TraceEventType,
    normalize_trace_value,
)


RUNTIME_CONFORMANCE_SCHEMA_VERSION = 1
RUNTIME_CONFORMANCE_CASE_IDS = frozenset({"M1", "M2", "M3", "M4"})
RUNTIME_CONFORMANCE_RUNTIMES = frozenset({"nanoharness", "langgraph"})


class ToolAttemptProjection(BaseModel):
    """One normalized tool-boundary attempt."""

    name: str = Field(min_length=1)
    arguments: Dict[str, Any] = Field(default_factory=dict)
    outcome: Literal["success", "error"]
    result: Any = None
    error_type: Optional[str] = None
    observation_delivered: bool = False

    @model_validator(mode="after")
    def validate_outcome(self):
        if self.outcome == "error" and not self.error_type:
            raise ValueError("error outcome requires error_type")
        if self.outcome == "success" and self.error_type is not None:
            raise ValueError("success outcome cannot contain error_type")
        return self


class PermissionDecisionProjection(BaseModel):
    """One normalized permission-boundary decision."""

    tool_name: str = Field(min_length=1)
    arguments: Dict[str, Any] = Field(default_factory=dict)
    allowed: bool
    denial: Optional[str] = None


class SemanticProjection(BaseModel):
    """Runtime-neutral facts retained from one ScenarioReport."""

    schema_version: int = RUNTIME_CONFORMANCE_SCHEMA_VERSION
    case_id: str = Field(min_length=1)
    runtime: str = Field(min_length=1)
    run_status: Optional[str] = None
    stop_reason: Optional[str] = None
    goal_achieved: Optional[bool] = None
    report_passed: bool
    execution_error_type: Optional[str] = None
    tool_attempts: List[ToolAttemptProjection] = Field(default_factory=list)
    permission_decisions: List[PermissionDecisionProjection] = Field(
        default_factory=list
    )
    recovery_observed: bool = False
    lifecycle_start_count: int = Field(ge=0)
    lifecycle_end_count: int = Field(ge=0)
    lifecycle_paired: bool
    event_ids_unique: bool
    sequences_monotonic: bool
    trace_ids_consistent: bool
    scenario_round_trip: bool
    report_round_trip: bool


class ProjectionExpectation(BaseModel):
    """Exact runtime-neutral facts declared before case execution."""

    case_id: str = Field(min_length=1)
    run_status: Optional[str] = None
    stop_reason: Optional[str] = None
    goal_achieved: Optional[bool] = None
    report_passed: bool
    execution_error_type: Optional[str] = None
    tool_attempts: List[ToolAttemptProjection] = Field(default_factory=list)
    permission_decisions: List[PermissionDecisionProjection] = Field(
        default_factory=list
    )
    recovery_observed: bool = False
    lifecycle_start_count: int = Field(ge=0)
    lifecycle_end_count: int = Field(ge=0)
    lifecycle_paired: bool
    event_ids_unique: bool
    sequences_monotonic: bool
    trace_ids_consistent: bool
    scenario_round_trip: bool
    report_round_trip: bool


class ProjectionMismatch(BaseModel):
    """One field-level disagreement retained for audit."""

    path: str = Field(min_length=1)
    expected: Any = None
    nanoharness: Any = None
    langgraph: Any = None


class CaseConformanceResult(BaseModel):
    """Paired result for one M1--M4 case."""

    case_id: str = Field(min_length=1)
    passed: bool
    nanoharness: SemanticProjection
    langgraph: SemanticProjection
    mismatches: List[ProjectionMismatch] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_comparison(self):
        if self.passed and self.mismatches:
            raise ValueError("passing comparison cannot contain mismatches")
        if self.nanoharness.case_id != self.case_id:
            raise ValueError("NanoHarness projection case_id does not match")
        if self.langgraph.case_id != self.case_id:
            raise ValueError("LangGraph projection case_id does not match")
        if self.nanoharness.runtime != "nanoharness":
            raise ValueError("NanoHarness projection runtime does not match")
        if self.langgraph.runtime != "langgraph":
            raise ValueError("LangGraph projection runtime does not match")
        return self


class RuntimeCaseEvidence(BaseModel):
    """Unmodified runtime report paired with its normalized projection."""

    case_id: str = Field(min_length=1)
    runtime: str = Field(min_length=1)
    scenario: Scenario
    report: ScenarioReport
    projection: SemanticProjection

    @model_validator(mode="after")
    def validate_identity(self):
        if self.scenario.scenario_id != self.case_id:
            raise ValueError("Scenario case_id does not match evidence")
        if self.report.scenario_id != self.case_id:
            raise ValueError("ScenarioReport case_id does not match evidence")
        if self.projection.case_id != self.case_id:
            raise ValueError("projection case_id does not match evidence")
        if self.projection.runtime != self.runtime:
            raise ValueError("projection runtime does not match evidence")
        return self


class RuntimeConformanceReport(BaseModel):
    """Auditable eight-cell M1--M4 cross-runtime experiment report."""

    schema_version: int = RUNTIME_CONFORMANCE_SCHEMA_VERSION
    experiment_id: str = Field(min_length=1)
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    runtime_versions: Dict[str, str]
    started_at: datetime
    finished_at: datetime
    cells: List[RuntimeCaseEvidence]
    comparisons: List[CaseConformanceResult]
    infrastructure_errors: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_report(self):
        if self.started_at.utcoffset() is None or self.finished_at.utcoffset() is None:
            raise ValueError("conformance timestamps require timezone offsets")
        keys = [(cell.case_id, cell.runtime) for cell in self.cells]
        if len(keys) != len(set(keys)):
            raise ValueError("runtime/case cells must be unique")
        required = {
            (case_id, runtime)
            for case_id in RUNTIME_CONFORMANCE_CASE_IDS
            for runtime in RUNTIME_CONFORMANCE_RUNTIMES
        }
        if set(keys) != required:
            raise ValueError("conformance report requires M1--M4 for both runtimes")
        comparison_ids = [item.case_id for item in self.comparisons]
        if len(comparison_ids) != len(set(comparison_ids)):
            raise ValueError("case comparisons must be unique")
        if set(comparison_ids) != RUNTIME_CONFORMANCE_CASE_IDS:
            raise ValueError("conformance report requires M1--M4 comparisons")
        return self


def project_scenario_report(
    case_id: str,
    runtime: str,
    scenario: Scenario,
    report: ScenarioReport,
) -> SemanticProjection:
    """Project one raw ScenarioReport without altering runtime-private events."""

    events = report.trace.events
    tool_attempts = []
    for event in events:
        if event.event_type is TraceEventType.TOOL_EXCHANGE:
            tool_attempts.append(
                ToolAttemptProjection(
                    name=str(event.payload.get("name", "")),
                    arguments=normalize_trace_value(
                        event.payload.get("arguments") or {}
                    ),
                    outcome="success",
                    result=normalize_trace_value(event.payload.get("result")),
                    observation_delivered=bool(
                        event.payload.get("observation_delivered", True)
                    ),
                )
            )
        elif event.event_type is TraceEventType.TOOL_ERROR:
            tool_attempts.append(
                ToolAttemptProjection(
                    name=str(event.payload.get("name", "")),
                    arguments=normalize_trace_value(
                        event.payload.get("arguments") or {}
                    ),
                    outcome="error",
                    error_type=str(event.payload.get("error_type", "Exception")),
                    observation_delivered=bool(
                        event.payload.get("observation_delivered", False)
                    ),
                )
            )

    permissions = [
        PermissionDecisionProjection(
            tool_name=str(event.payload.get("tool_name", "")),
            arguments=normalize_trace_value(event.payload.get("arguments") or {}),
            allowed=bool(event.payload.get("allowed")),
            denial=(
                str(event.payload["denial"])
                if event.payload.get("denial") is not None
                else None
            ),
        )
        for event in events
        if event.event_type is TraceEventType.PERMISSION_DECISION
    ]

    error_indexes = [
        (index, attempt.name)
        for index, attempt in enumerate(tool_attempts)
        if attempt.outcome == "error"
    ]
    recovery_observed = any(
        later.name == name and later.outcome == "success"
        for index, name in error_indexes
        for later in tool_attempts[index + 1 :]
    ) or any(
        event.event_type is TraceEventType.CUSTOM
        and event.payload.get("adapter_event") == "recovery"
        for event in events
    )

    starts = [
        event.sequence
        for event in events
        if event.event_type is TraceEventType.TASK_STARTED
    ]
    ends = [
        event.sequence
        for event in events
        if event.event_type is TraceEventType.TASK_COMPLETED
    ]
    sequences = [event.sequence for event in events]
    event_ids = [event.event_id for event in events]
    lifecycle_paired = (
        len(starts) == len(ends)
        and bool(starts)
        and all(start < end for start, end in zip(starts, ends))
    )
    scenario_round_trip = _round_trips(scenario, Scenario)
    report_round_trip = _round_trips(report, ScenarioReport)
    result = report.result
    return SemanticProjection(
        case_id=case_id,
        runtime=runtime,
        run_status=result.status.value if result is not None else None,
        stop_reason=result.stop_reason.value if result is not None else None,
        goal_achieved=(
            result.evaluation.achieved if result is not None else None
        ),
        report_passed=report.passed,
        execution_error_type=(
            report.execution_error.error_type
            if report.execution_error is not None
            else None
        ),
        tool_attempts=tool_attempts,
        permission_decisions=permissions,
        recovery_observed=recovery_observed,
        lifecycle_start_count=len(starts),
        lifecycle_end_count=len(ends),
        lifecycle_paired=lifecycle_paired,
        event_ids_unique=len(event_ids) == len(set(event_ids)),
        sequences_monotonic=all(
            current < following
            for current, following in zip(sequences, sequences[1:])
        ),
        trace_ids_consistent=all(
            event.trace_id == report.trace.trace_id for event in events
        ),
        scenario_round_trip=scenario_round_trip,
        report_round_trip=report_round_trip,
    )


def compare_projections(
    expected: ProjectionExpectation,
    nanoharness: SemanticProjection,
    langgraph: SemanticProjection,
    *,
    stop_reason_equivalences: Optional[Dict[str, str]] = None,
) -> CaseConformanceResult:
    """Compare both projections with an exact preregistered expectation."""

    if nanoharness.case_id != expected.case_id:
        raise ValueError("NanoHarness projection case_id does not match expectation")
    if langgraph.case_id != expected.case_id:
        raise ValueError("LangGraph projection case_id does not match expectation")
    mappings = stop_reason_equivalences or {}
    expected_value = expected.model_dump(mode="json")
    nano_value = nanoharness.model_dump(
        mode="json", exclude={"schema_version", "runtime"}
    )
    graph_value = langgraph.model_dump(
        mode="json", exclude={"schema_version", "runtime"}
    )
    for value in (nano_value, graph_value):
        reason = value.get("stop_reason")
        value["stop_reason"] = mappings.get(reason, reason)
    mismatches: List[ProjectionMismatch] = []
    _compare_values(expected_value, nano_value, graph_value, "", mismatches)
    return CaseConformanceResult(
        case_id=expected.case_id,
        passed=not mismatches,
        nanoharness=nanoharness,
        langgraph=langgraph,
        mismatches=mismatches,
    )


def _round_trips(value: BaseModel, model_type: type[BaseModel]) -> bool:
    try:
        restored = model_type.model_validate_json(value.model_dump_json())
    except Exception:
        return False
    return restored == value


def _compare_values(
    expected: Any,
    nanoharness: Any,
    langgraph: Any,
    path: str,
    mismatches: List[ProjectionMismatch],
) -> None:
    if isinstance(expected, Mapping):
        for key in expected:
            child = f"{path}.{key}" if path else str(key)
            _compare_values(
                expected[key],
                nanoharness.get(key) if isinstance(nanoharness, Mapping) else None,
                langgraph.get(key) if isinstance(langgraph, Mapping) else None,
                child,
                mismatches,
            )
        return
    if isinstance(expected, Sequence) and not isinstance(expected, (str, bytes)):
        nano_values = (
            nanoharness
            if isinstance(nanoharness, Sequence)
            and not isinstance(nanoharness, (str, bytes))
            else []
        )
        graph_values = (
            langgraph
            if isinstance(langgraph, Sequence)
            and not isinstance(langgraph, (str, bytes))
            else []
        )
        if len(expected) != len(nano_values) or len(expected) != len(graph_values):
            mismatches.append(
                ProjectionMismatch(
                    path=path,
                    expected=expected,
                    nanoharness=nanoharness,
                    langgraph=langgraph,
                )
            )
            return
        for index, item in enumerate(expected):
            _compare_values(
                item,
                nano_values[index],
                graph_values[index],
                f"{path}[{index}]",
                mismatches,
            )
        return
    if nanoharness != expected or langgraph != expected:
        mismatches.append(
            ProjectionMismatch(
                path=path,
                expected=expected,
                nanoharness=nanoharness,
                langgraph=langgraph,
            )
        )
