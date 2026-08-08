from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from nanoharness.core.schema import EvaluationResult, RunResult, RunStatus, StopReason
from nanoharness.testing.conformance import (
    RUNTIME_CONFORMANCE_SCHEMA_VERSION,
    CaseConformanceResult,
    PermissionDecisionProjection,
    ProjectionMismatch,
    RuntimeCaseEvidence,
    RuntimeConformanceReport,
    SemanticProjection,
    ToolAttemptProjection,
)
from nanoharness.testing.scenario import Scenario, ScenarioReport
from nanoharness.testing.trace import TraceRecorder


def _projection(case_id: str, runtime: str) -> SemanticProjection:
    return SemanticProjection(
        case_id=case_id,
        runtime=runtime,
        run_status="completed",
        stop_reason="normal_termination",
        goal_achieved=True,
        report_passed=True,
        execution_error_type=None,
        tool_attempts=[
            ToolAttemptProjection(
                name="lookup_order",
                arguments={"order_id": 7},
                outcome="success",
                result={"status": "open"},
                observation_delivered=True,
            )
        ],
        permission_decisions=[
            PermissionDecisionProjection(
                tool_name="lookup_order",
                arguments={"order_id": 7},
                allowed=True,
            )
        ],
        recovery_observed=False,
        lifecycle_start_count=1,
        lifecycle_end_count=1,
        lifecycle_paired=True,
        event_ids_unique=True,
        sequences_monotonic=True,
        trace_ids_consistent=True,
        scenario_round_trip=True,
        report_round_trip=True,
    )


def _evidence(case_id: str, runtime: str) -> RuntimeCaseEvidence:
    scenario = Scenario(scenario_id=case_id, query="run the case")
    trace = TraceRecorder(trace_id=f"trace-{runtime}-{case_id}").snapshot()
    result = RunResult(
        status=RunStatus.COMPLETED,
        stop_reason=StopReason.SUBJECT_COMPLETED,
        final_answer="done",
        evaluation=EvaluationResult(achieved=True, confidence=1.0),
    )
    report = ScenarioReport(
        scenario_id=case_id,
        passed=True,
        result=result,
        trace=trace,
    )
    return RuntimeCaseEvidence(
        case_id=case_id,
        runtime=runtime,
        scenario=scenario,
        report=report,
        projection=_projection(case_id, runtime),
    )


def _report() -> RuntimeConformanceReport:
    cases = ("M1", "M2", "M3", "M4")
    cells = [
        _evidence(case_id, runtime)
        for case_id in cases
        for runtime in ("nanoharness", "langgraph")
    ]
    comparisons = [
        CaseConformanceResult(
            case_id=case_id,
            passed=True,
            nanoharness=_projection(case_id, "nanoharness"),
            langgraph=_projection(case_id, "langgraph"),
        )
        for case_id in cases
    ]
    return RuntimeConformanceReport(
        experiment_id="runtime-conformance-v1",
        manifest_digest="a" * 64,
        artifact_revision="b" * 40,
        runtime_versions={"nanoharness": "0.1.0", "langgraph": "1.2.10"},
        started_at=datetime(2026, 8, 8, tzinfo=timezone.utc),
        finished_at=datetime(2026, 8, 8, tzinfo=timezone.utc),
        cells=cells,
        comparisons=comparisons,
    )


def test_runtime_conformance_report_round_trips_with_eight_cells():
    report = _report()

    restored = RuntimeConformanceReport.model_validate_json(report.model_dump_json())

    assert restored == report
    assert restored.schema_version == RUNTIME_CONFORMANCE_SCHEMA_VERSION
    assert len(restored.cells) == 8
    assert len(restored.comparisons) == 4


def test_runtime_conformance_report_rejects_duplicate_runtime_case_cell():
    report = _report()
    duplicate = report.cells[0].model_copy(deep=True)

    with pytest.raises(ValidationError, match="runtime/case cells must be unique"):
        RuntimeConformanceReport.model_validate(
            report.model_copy(update={"cells": [*report.cells, duplicate]}).model_dump()
        )


def test_runtime_conformance_report_requires_all_m1_m4_cells():
    report = _report()

    with pytest.raises(ValidationError, match="requires M1--M4 for both runtimes"):
        RuntimeConformanceReport.model_validate(
            report.model_copy(update={"cells": report.cells[:-1]}).model_dump()
        )


def test_passing_case_cannot_contain_mismatches():
    left = _projection("M1", "nanoharness")
    right = _projection("M1", "langgraph")

    with pytest.raises(ValidationError, match="passing comparison cannot contain"):
        CaseConformanceResult(
            case_id="M1",
            passed=True,
            nanoharness=left,
            langgraph=right,
            mismatches=[
                ProjectionMismatch(
                    path="goal_achieved",
                    expected=True,
                    nanoharness=True,
                    langgraph=False,
                )
            ],
        )


def test_tool_attempt_requires_error_type_for_error_outcome():
    with pytest.raises(ValidationError, match="error outcome requires error_type"):
        ToolAttemptProjection(
            name="lookup_order",
            arguments={"order_id": 7},
            outcome="error",
        )
