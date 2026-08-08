from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from nanoharness.core.schema import EvaluationResult, RunResult, RunStatus, StopReason
from nanoharness.testing.conformance import (
    RUNTIME_CONFORMANCE_SCHEMA_VERSION,
    CaseConformanceResult,
    PermissionDecisionProjection,
    ProjectionExpectation,
    ProjectionMismatch,
    RuntimeCaseEvidence,
    RuntimeConformanceReport,
    SemanticProjection,
    ToolAttemptProjection,
    compare_projections,
    project_scenario_report,
)
from nanoharness.testing.scenario import Scenario, ScenarioReport
from nanoharness.testing.trace import TraceEventType, TraceRecorder


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


def _recovery_report(runtime: str, stop_reason: StopReason) -> tuple[Scenario, ScenarioReport]:
    scenario = Scenario(scenario_id="M2", query="look up order 7")
    recorder = TraceRecorder(trace_id=f"trace-{runtime}-M2")
    recorder.record(TraceEventType.TASK_STARTED, {"query": scenario.query})
    recorder.record(
        TraceEventType.TOOL_ERROR,
        {
            "name": "lookup_order",
            "arguments": {"order_id": 7},
            "error_type": "TransientToolError",
            "error_message": "retryable",
        },
    )
    recorder.record(
        TraceEventType.TOOL_EXCHANGE,
        {
            "name": "lookup_order",
            "arguments": {"order_id": 7},
            "result": {"status": "open"},
            "observation_delivered": True,
        },
    )
    result = RunResult(
        status=RunStatus.COMPLETED,
        stop_reason=stop_reason,
        final_answer="open",
        evaluation=EvaluationResult(achieved=True, confidence=1.0),
    )
    recorder.record(TraceEventType.TASK_COMPLETED, {"result": result})
    return scenario, ScenarioReport(
        scenario_id="M2",
        passed=True,
        result=result,
        trace=recorder.snapshot(),
    )


def test_project_scenario_report_extracts_boundaries_and_invariants():
    scenario, report = _recovery_report(
        "nanoharness", StopReason.MODEL_TERMINATED
    )

    projection = project_scenario_report(
        case_id="M2",
        runtime="nanoharness",
        scenario=scenario,
        report=report,
    )

    assert [attempt.outcome for attempt in projection.tool_attempts] == [
        "error",
        "success",
    ]
    assert projection.tool_attempts[0].error_type == "TransientToolError"
    assert projection.tool_attempts[1].result == {"status": "open"}
    assert projection.recovery_observed is True
    assert projection.lifecycle_start_count == 1
    assert projection.lifecycle_end_count == 1
    assert projection.lifecycle_paired is True
    assert projection.event_ids_unique is True
    assert projection.sequences_monotonic is True
    assert projection.trace_ids_consistent is True
    assert projection.scenario_round_trip is True
    assert projection.report_round_trip is True


def test_compare_projections_maps_only_declared_stop_reason_equivalence():
    scenario, nano_report = _recovery_report(
        "nanoharness", StopReason.MODEL_TERMINATED
    )
    _, graph_report = _recovery_report(
        "langgraph", StopReason.SUBJECT_COMPLETED
    )
    nano = project_scenario_report("M2", "nanoharness", scenario, nano_report)
    graph = project_scenario_report("M2", "langgraph", scenario, graph_report)
    expected = ProjectionExpectation(
        case_id="M2",
        run_status="completed",
        stop_reason="normal_termination",
        goal_achieved=True,
        report_passed=True,
        execution_error_type=None,
        tool_attempts=nano.tool_attempts,
        permission_decisions=[],
        recovery_observed=True,
        lifecycle_start_count=1,
        lifecycle_end_count=1,
        lifecycle_paired=True,
        event_ids_unique=True,
        sequences_monotonic=True,
        trace_ids_consistent=True,
        scenario_round_trip=True,
        report_round_trip=True,
    )

    result = compare_projections(
        expected,
        nano,
        graph,
        stop_reason_equivalences={
            "model_terminated": "normal_termination",
            "subject_completed": "normal_termination",
        },
    )

    assert result.passed is True
    assert result.mismatches == []
    assert result.nanoharness.stop_reason == "model_terminated"
    assert result.langgraph.stop_reason == "subject_completed"


def test_compare_projections_reports_exact_nested_mismatch_path():
    scenario, nano_report = _recovery_report(
        "nanoharness", StopReason.MODEL_TERMINATED
    )
    _, graph_report = _recovery_report(
        "langgraph", StopReason.SUBJECT_COMPLETED
    )
    graph_report.trace.events[1].payload["arguments"]["order_id"] = 8
    nano = project_scenario_report("M2", "nanoharness", scenario, nano_report)
    graph = project_scenario_report("M2", "langgraph", scenario, graph_report)
    expected = ProjectionExpectation.model_validate(
        nano.model_dump(exclude={"schema_version", "runtime"})
    )

    result = compare_projections(
        expected,
        nano,
        graph,
        stop_reason_equivalences={},
    )

    assert result.passed is False
    assert any(
        mismatch.path == "tool_attempts[0].arguments.order_id"
        for mismatch in result.mismatches
    )
