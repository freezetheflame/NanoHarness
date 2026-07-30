from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from nanoharness.core.schema import (
    EvaluationResult,
    RunResult,
    RunStatus,
    StopReason,
)
from nanoharness.testing import (
    AgentTrace,
    CoverageCollector,
    CoverageConfigurationError,
    CoverageGateError,
    CoverageKind,
    CoverageModel,
    CoverageModelStatus,
    CoverageRun,
    CoverageTarget,
    FaultAction,
    FaultApplication,
    FaultComponent,
    FaultReport,
    FaultRule,
    OracleVerdict,
    ScenarioReport,
    TraceEvent,
    TraceEventType,
    TRACE_SCHEMA_VERSION,
    coverage_target_digest,
)


def _event(trace_id, sequence, event_type, payload=None):
    return TraceEvent(
        event_id=f"{trace_id}:{sequence}",
        trace_id=trace_id,
        sequence=sequence,
        event_type=event_type,
        timestamp=float(sequence),
        payload=payload or {},
    )


def _report(*, trace_id="trace-1", include_delete=False, passed=True):
    events = [
        _event(trace_id, 0, TraceEventType.TASK_STARTED, {"data": "echo hello"}),
        _event(
            trace_id,
            1,
            TraceEventType.TOOL_EXCHANGE,
            {"name": "echo", "arguments": {"text": "hello"}, "result": "hello"},
        ),
        _event(
            trace_id,
            2,
            TraceEventType.TOOL_ERROR,
            {"name": "lookup", "arguments": {"key": "missing"}},
        ),
    ]
    if include_delete:
        events.append(
            _event(
                trace_id,
                len(events),
                TraceEventType.TOOL_EXCHANGE,
                {"name": "delete", "arguments": {"path": "tmp"}, "result": True},
            )
        )
    events.append(
        _event(trace_id, len(events), TraceEventType.TASK_COMPLETED, {})
    )
    return ScenarioReport(
        scenario_id="coverage-scenario",
        passed=passed,
        result=RunResult(
            status=RunStatus.COMPLETED,
            stop_reason=StopReason.MODEL_TERMINATED,
            evaluation=EvaluationResult(achieved=True),
        ),
        trace=AgentTrace(
            trace_id=trace_id,
            started_at=0,
            events=events,
            metadata={
                "execution_mode": "fault_injected",
                "fault_plan_id": "fault-plan",
            },
        ),
        verdicts=[
            OracleVerdict(
                oracle_id="goal",
                kind="goal_achievement",
                passed=True,
                message="goal achieved",
            )
        ],
    )


def _fault_report():
    rule = FaultRule(
        rule_id="stale",
        component=FaultComponent.TOOL,
        action=FaultAction.TOOL_RESULT_STALE,
        tool_name="echo",
        replacement="old",
    )
    return FaultReport(
        plan_id="fault-plan",
        configured_rules=[rule],
        applications=[
            FaultApplication(
                sequence=0,
                rule_id="stale",
                component=FaultComponent.TOOL,
                action=FaultAction.TOOL_RESULT_STALE,
                boundary_index=0,
                tool_name="echo",
                effective=True,
            )
        ],
    )


def _target(target_id, kind, required=False, **parameters):
    return CoverageTarget(
        target_id=target_id,
        kind=kind,
        parameters=parameters,
        required=required,
    )


def _model():
    return CoverageModel(
        model_id="agent-behavior-v1",
        targets=[
            _target("tool.echo", CoverageKind.TOOL_CALL, tool_name="echo"),
            _target(
                "arg.echo.hello",
                CoverageKind.TOOL_ARGUMENT,
                tool_name="echo",
                argument="text",
                matcher="equals",
                value="hello",
            ),
            _target(
                "arg.echo.string",
                CoverageKind.TOOL_ARGUMENT,
                tool_name="echo",
                argument="text",
                matcher="type",
                value_type="string",
            ),
            _target(
                "arg.echo.regex",
                CoverageKind.TOOL_ARGUMENT,
                tool_name="echo",
                argument="text",
                matcher="regex",
                pattern="^hel",
            ),
            _target(
                "status.completed",
                CoverageKind.RUN_STATUS,
                status="completed",
            ),
            _target(
                "stop.model",
                CoverageKind.STOP_REASON,
                reason="model_terminated",
            ),
            _target(
                "lifecycle.start",
                CoverageKind.LIFECYCLE_EVENT,
                event_type="task_started",
            ),
            _target(
                "error.tool",
                CoverageKind.COMPONENT_ERROR,
                component="tool",
            ),
            _target(
                "oracle.goal.pass",
                CoverageKind.ORACLE_OUTCOME,
                oracle_kind="goal_achievement",
                passed=True,
            ),
            _target(
                "fault.stale",
                CoverageKind.FAULT_ACTION,
                action="tool_result_stale",
                effective=True,
            ),
            _target(
                "fault.stale.scenario-passed",
                CoverageKind.FAULT_OUTCOME,
                rule_id="stale",
                scenario_passed=True,
            ),
            _target(
                "mode.injected",
                CoverageKind.TRACE_METADATA,
                key="execution_mode",
                value="fault_injected",
            ),
            _target(
                "tool.delete",
                CoverageKind.TOOL_CALL,
                required=True,
                tool_name="delete",
            ),
        ],
    )


def test_collector_uses_explicit_denominator_and_preserves_evidence():
    coverage = CoverageCollector().collect(
        _model(),
        [CoverageRun(scenario_report=_report(), fault_report=_fault_report())],
    )

    assert coverage.covered_targets == 12
    assert coverage.total_targets == 13
    assert coverage.coverage_ratio == pytest.approx(12 / 13)
    assert coverage.uncovered_target_ids == ["tool.delete"]
    assert coverage.uncovered_required_target_ids == ["tool.delete"]
    echo = coverage.target_results[0]
    assert echo.hit_count == 1
    assert echo.evidence[0].event_id == "trace-1:1"
    assert echo.evidence[0].source == "trace"
    restored = type(coverage).model_validate_json(coverage.model_dump_json())
    assert restored == coverage


def test_coverage_aggregates_union_without_changing_model_denominator():
    first = CoverageRun(scenario_report=_report(), fault_report=_fault_report())
    second = _report(trace_id="trace-2", include_delete=True)

    coverage = CoverageCollector().collect(_model(), [first, second])

    assert coverage.coverage_ratio == 1.0
    assert coverage.total_targets == 13
    assert coverage.scenario_ids == ["coverage-scenario"]
    assert coverage.trace_ids == ["trace-1", "trace-2"]
    assert coverage.uncovered_required_target_ids == []


def test_collector_rejects_duplicate_or_unsupported_trace_evidence():
    report = _report()
    with pytest.raises(CoverageConfigurationError, match="Duplicate Trace IDs"):
        CoverageCollector().collect(_model(), [report, report])

    data = report.model_dump()
    data["trace"]["schema_version"] = TRACE_SCHEMA_VERSION + 1
    unsupported = ScenarioReport.model_validate(data)
    with pytest.raises(CoverageConfigurationError, match="unsupported schema"):
        CoverageCollector().collect(_model(), [unsupported])


def test_coverage_run_rejects_mismatched_fault_provenance():
    fault_report = _fault_report().model_copy(update={"plan_id": "another-plan"})

    with pytest.raises(ValidationError, match="does not match Trace plan"):
        CoverageRun(scenario_report=_report(), fault_report=fault_report)


def test_coverage_gate_is_optional_and_only_uses_explicit_threshold():
    coverage = CoverageCollector().collect(_model(), [_report()])

    with pytest.raises(CoverageGateError, match="required targets uncovered"):
        coverage.evaluate_gate()
    with pytest.raises(CoverageGateError, match="coverage ratio"):
        coverage.evaluate_gate(minimum_ratio=1.0)
    with pytest.raises(ValueError, match="between 0 and 1"):
        coverage.evaluate_gate(minimum_ratio=1.1)


def test_empty_target_universe_has_no_invented_ratio():
    coverage = CoverageCollector().collect(
        CoverageModel(model_id="empty"),
        [_report()],
    )

    assert coverage.total_targets == 0
    assert coverage.coverage_ratio is None
    assert coverage.dimensions == []


def test_legacy_v1_coverage_model_remains_readable():
    model = CoverageModel(
        schema_version=1,
        model_id="legacy",
        targets=[_target("echo", CoverageKind.TOOL_CALL, tool_name="echo")],
    )

    coverage = CoverageCollector().collect(model, [_report()])

    assert coverage.coverage_ratio == 1.0
    assert coverage.model.schema_version == 1


@pytest.mark.parametrize(
    "target, message",
    [
        (
            {"target_id": "missing", "kind": CoverageKind.TOOL_CALL},
            "misses parameters",
        ),
        (
            {
                "target_id": "bad-status",
                "kind": CoverageKind.RUN_STATUS,
                "parameters": {"status": "unknown"},
            },
            "not a valid RunStatus",
        ),
        (
            {
                "target_id": "bad-matcher",
                "kind": CoverageKind.TOOL_ARGUMENT,
                "parameters": {
                    "tool_name": "echo",
                    "argument": "text",
                    "matcher": "range",
                    "minimum": 2,
                    "maximum": 1,
                },
            },
            "minimum cannot exceed",
        ),
        (
            {
                "target_id": "ambiguous-oracle",
                "kind": CoverageKind.ORACLE_OUTCOME,
                "parameters": {
                    "oracle_id": "goal",
                    "oracle_kind": "goal_achievement",
                    "passed": True,
                },
            },
            "exactly one",
        ),
        (
            {
                "target_id": "typo",
                "kind": CoverageKind.TOOL_CALL,
                "parameters": {"tool_name": "echo", "tool": "echo"},
            },
            "unknown parameters",
        ),
        (
            {
                "target_id": "bad-regex",
                "kind": CoverageKind.TOOL_ARGUMENT,
                "parameters": {
                    "tool_name": "echo",
                    "argument": "text",
                    "matcher": "regex",
                    "pattern": "[",
                },
            },
            "Invalid regex matcher pattern",
        ),
        (
            {
                "target_id": "none-bound",
                "kind": CoverageKind.TOOL_ARGUMENT,
                "parameters": {
                    "tool_name": "echo",
                    "argument": "text",
                    "matcher": "range",
                    "minimum": None,
                },
            },
            "bounds must be numeric",
        ),
    ],
)
def test_target_configuration_fails_before_collection(target, message):
    with pytest.raises((ValidationError, ValueError), match=message):
        CoverageTarget.model_validate(target)


def test_model_rejects_duplicate_target_ids():
    target = _target("same", CoverageKind.TOOL_CALL, tool_name="echo")

    with pytest.raises(ValidationError, match="Duplicate coverage target IDs"):
        CoverageModel(model_id="duplicates", targets=[target, target])


def test_repository_coverage_template_is_valid_but_explicitly_not_frozen():
    path = Path(__file__).parents[1] / "research" / "coverage" / "model_template.json"

    model = CoverageModel.model_validate_json(path.read_text())

    assert len(model.targets) == 32
    assert model.status is CoverageModelStatus.TEMPLATE


def test_frozen_model_binds_subject_derivation_and_target_digest():
    targets = [_target("echo", CoverageKind.TOOL_CALL, tool_name="echo")]
    model = CoverageModel(
        model_id="frozen-v1",
        status=CoverageModelStatus.FROZEN,
        subject_id="runtime@revision",
        derivation="Targets derived from the frozen tool schema.",
        targets=targets,
        frozen_at=datetime.now(timezone.utc),
        target_digest=coverage_target_digest(targets),
    )

    report = CoverageCollector().collect(model, [_report()], require_frozen=True)

    assert report.coverage_ratio == 1.0
    model.targets[0].parameters["tool_name"] = "changed"
    with pytest.raises(CoverageConfigurationError, match="changed after freezing"):
        CoverageCollector().collect(model, [_report()], require_frozen=True)


def test_experiment_mode_rejects_template_or_incorrect_frozen_digest():
    template = CoverageModel.model_validate_json(
        (
            Path(__file__).parents[1]
            / "research"
            / "coverage"
            / "model_template.json"
        ).read_text()
    )
    with pytest.raises(CoverageConfigurationError, match="is not frozen"):
        CoverageCollector().collect(template, [_report()], require_frozen=True)

    with pytest.raises(ValidationError, match="target_digest does not match"):
        CoverageModel(
            model_id="bad-frozen",
            status=CoverageModelStatus.FROZEN,
            subject_id="runtime@revision",
            derivation="Declared targets.",
            targets=[_target("echo", CoverageKind.TOOL_CALL, tool_name="echo")],
            frozen_at=datetime.now(timezone.utc),
            target_digest="0" * 64,
        )


def test_argument_missing_range_and_one_of_classes_are_deterministic():
    model = CoverageModel(
        model_id="argument-classes",
        targets=[
            _target(
                "missing",
                CoverageKind.TOOL_ARGUMENT,
                tool_name="echo",
                argument="optional",
                matcher="missing",
            ),
            _target(
                "one-of",
                CoverageKind.TOOL_ARGUMENT,
                tool_name="echo",
                argument="text",
                matcher="one_of",
                values=["hello", "hi"],
            ),
            _target(
                "not-range",
                CoverageKind.TOOL_ARGUMENT,
                tool_name="echo",
                argument="text",
                matcher="range",
                minimum=1,
                maximum=5,
            ),
        ],
    )

    coverage = CoverageCollector().collect(model, [_report()])

    assert [result.covered for result in coverage.target_results] == [True, True, False]


def test_execution_error_target_can_match_type_or_any_error():
    report = _report()
    data = report.model_dump()
    data["result"] = None
    data["execution_error"] = {"error_type": "RuntimeError", "message": "boom"}
    report = ScenarioReport.model_validate(data)
    model = CoverageModel(
        model_id="execution-errors",
        targets=[
            _target("any", CoverageKind.EXECUTION_ERROR),
            _target(
                "runtime",
                CoverageKind.EXECUTION_ERROR,
                error_type="RuntimeError",
            ),
            _target(
                "value",
                CoverageKind.EXECUTION_ERROR,
                error_type="ValueError",
            ),
        ],
    )

    coverage = CoverageCollector().collect(model, [report])

    assert [result.covered for result in coverage.target_results] == [True, True, False]


def test_runtime_boundary_targets_cover_context_state_hook_and_permission():
    report = _report()
    sequence = report.trace.events[-1].sequence + 1
    report.trace.events.extend([
        _event(
            report.trace.trace_id,
            sequence,
            TraceEventType.CONTEXT_MESSAGE_ADDED,
            {"message": {"role": "user", "content": "hello"}},
        ),
        _event(
            report.trace.trace_id,
            sequence + 1,
            TraceEventType.STATE_SAVED,
            {"state": {"current_step": 2}},
        ),
        _event(
            report.trace.trace_id,
            sequence + 2,
            TraceEventType.HOOK_COMPLETED,
            {"stage": "on_task_end"},
        ),
        _event(
            report.trace.trace_id,
            sequence + 3,
            TraceEventType.PERMISSION_DECISION,
            {"tool_name": "delete", "allowed": False},
        ),
    ])
    model = CoverageModel(
        model_id="runtime-boundaries",
        targets=[
            _target(
                "context.user",
                CoverageKind.CONTEXT_MESSAGE,
                role="user",
            ),
            _target(
                "state.step.two",
                CoverageKind.STATE_VALUE,
                operation="saved",
                key="current_step",
                matcher="equals",
                value=2,
            ),
            _target(
                "hook.task-end",
                CoverageKind.HOOK_STAGE,
                stage="on_task_end",
                outcome="completed",
            ),
            _target(
                "permission.delete.denied",
                CoverageKind.PERMISSION_DECISION,
                tool_name="delete",
                allowed=False,
            ),
        ],
    )

    coverage = CoverageCollector().collect(model, [report])

    assert coverage.coverage_ratio == 1.0
    assert all(result.hit_count == 1 for result in coverage.target_results)
