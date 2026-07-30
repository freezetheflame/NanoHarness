from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from nanoharness.components.tools.dict_registry import DictToolRegistry
from nanoharness.core.schema import (
    EvaluationResult,
    RunResult,
    RunStatus,
    StopReason,
)
from nanoharness.testing import (
    CallableSubjectAdapter,
    ExperimentCell,
    ExperimentConfigurationError,
    ExperimentManifest,
    ExperimentRunner,
    FaultAction,
    FaultComponent,
    FaultExperimentManifest,
    FaultInjectingToolRegistry,
    FaultPlan,
    FaultRule,
    MutationStatus,
    OracleKind,
    OracleSpec,
    RecordingToolRegistry,
    Scenario,
    SubjectFaultCampaignRunner,
    SubjectIdentity,
    TraceEventType,
    experiment_manifest_digest,
    fault_experiment_manifest_digest,
)


def _identity(**updates):
    data = {
        "subject_id": "external-runtime@abc123",
        "runtime": "external-runtime",
        "version": "1.2.3",
        "revision": "abc123",
        "source_url": "https://example.test/external-runtime",
        "independently_developed": True,
    }
    data.update(updates)
    return SubjectIdentity(**data)


def _scenario():
    return Scenario(
        scenario_id="external-echo",
        query="echo hello",
        oracles=[
            OracleSpec(
                kind=OracleKind.GOAL_ACHIEVEMENT,
                parameters={"expected": True},
            ),
            OracleSpec(kind=OracleKind.LIFECYCLE),
        ],
    )


def _executor(scenario, recorder):
    recorder.record(
        TraceEventType.CUSTOM,
        {
            "adapter": "external-runtime",
            "kind": "state_snapshot",
            "seed": scenario.seed,
        },
    )
    return RunResult(
        status=RunStatus.COMPLETED,
        stop_reason=StopReason.MODEL_TERMINATED,
        final_answer="hello",
        evaluation=EvaluationResult(
            achieved=True,
            confidence=1.0,
            explanation="deterministic external scorer",
        ),
    )


def _manifest(identity=None, scenario=None, seeds=None):
    identity = identity or _identity()
    cell = ExperimentCell(
        cell_id="external:echo",
        subject_id=identity.subject_id,
        scenario=scenario or _scenario(),
        seeds=seeds or [11, 29],
    )
    frozen_at = datetime(2026, 7, 30, tzinfo=timezone.utc)
    return ExperimentManifest(
        experiment_id="pilot-v1",
        subjects=[identity],
        cells=[cell],
        frozen_at=frozen_at,
        manifest_digest=experiment_manifest_digest(
            "pilot-v1",
            [identity],
            [cell],
            frozen_at=frozen_at,
        ),
    )


def test_external_identity_requires_source_provenance():
    with pytest.raises(ValidationError, match="stable source_url"):
        _identity(source_url=None)
    with pytest.raises(ValidationError, match="immutable revision"):
        _identity(revision="main")


def test_callable_adapter_records_lifecycle_subject_and_custom_events():
    adapter = CallableSubjectAdapter(_identity(), _executor)

    report = adapter.run(_scenario())

    assert report.passed is True
    assert [event.event_type for event in report.trace.events] == [
        TraceEventType.TASK_STARTED,
        TraceEventType.CUSTOM,
        TraceEventType.TASK_COMPLETED,
    ]
    assert report.trace.metadata["subject"]["independently_developed"] is True
    assert adapter.identity == _identity()


def test_adapter_normalizes_executor_errors_and_wrong_result_types():
    def fail(scenario, recorder):
        raise RuntimeError("external crash")

    failed = CallableSubjectAdapter(_identity(), fail).run(
        Scenario(scenario_id="fail", query="fail")
    )
    wrong = CallableSubjectAdapter(
        _identity(),
        lambda scenario, recorder: {"not": "RunResult"},
    ).run(Scenario(scenario_id="wrong", query="wrong"))

    assert failed.passed is False
    assert failed.execution_error.error_type == "RuntimeError"
    assert wrong.execution_error.error_type == "TypeError"


def test_manifest_allows_repeated_seed_but_rejects_ambiguous_scenario_seed():
    repeated = ExperimentCell(
        cell_id="repeated-seed",
        subject_id=_identity().subject_id,
        scenario=_scenario(),
        seeds=[1, 1],
    )
    assert repeated.seeds == [1, 1]

    seeded = _scenario().model_copy(update={"seed": 7})
    with pytest.raises(ValidationError, match="Scenario seed must be unset"):
        ExperimentCell(
            cell_id="ambiguous-seed",
            subject_id=_identity().subject_id,
            scenario=seeded,
            seeds=[7],
        )


def test_manifest_rejects_unknown_subject_and_bad_digest():
    cell = ExperimentCell(
        cell_id="unknown",
        subject_id="missing",
        scenario=_scenario(),
        seeds=[1],
    )
    frozen_at = datetime.now(timezone.utc)
    with pytest.raises(ValidationError, match="unknown subjects"):
        ExperimentManifest(
            experiment_id="unknown",
            subjects=[_identity()],
            cells=[cell],
            frozen_at=frozen_at,
            manifest_digest=experiment_manifest_digest(
                "unknown",
                [_identity()],
                [cell],
                frozen_at=frozen_at,
            ),
        )
    with pytest.raises(ValidationError, match="manifest_digest"):
        ExperimentManifest(
            experiment_id="bad-digest",
            subjects=[_identity()],
            cells=[
                ExperimentCell(
                    cell_id="cell",
                    subject_id=_identity().subject_id,
                    scenario=_scenario(),
                    seeds=[1],
                )
            ],
            frozen_at=datetime.now(timezone.utc),
            manifest_digest="0" * 64,
        )


def test_experiment_runner_preserves_seed_order_raw_reports_and_summary():
    adapter = CallableSubjectAdapter(_identity(), _executor)
    ticks = iter([0.0, 0.01, 0.02, 0.05])
    wall_ticks = iter([
        datetime(2026, 7, 30, 1, tzinfo=timezone.utc),
        datetime(2026, 7, 30, 2, tzinfo=timezone.utc),
    ])

    report = ExperimentRunner(
        [adapter],
        clock=lambda: next(ticks),
        wall_clock=lambda: next(wall_ticks),
    ).run(_manifest())

    assert [item.seed for item in report.observations] == [11, 29]
    assert [item.repetition for item in report.observations] == [0, 1]
    assert [item.duration_ms for item in report.observations] == pytest.approx(
        [10.0, 30.0]
    )
    assert all(item.report.passed for item in report.observations)
    assert report.subjects[0].runs == 2
    assert report.subjects[0].pass_rate == 1.0
    restored = type(report).model_validate_json(report.model_dump_json())
    assert restored == report
    tampered = report.model_dump()
    tampered["observations"][0]["seed"] = 999
    with pytest.raises(ValidationError, match="does not match frozen Manifest"):
        type(report).model_validate(tampered)


def test_runner_rejects_manifest_mutation_or_adapter_identity_drift_before_run():
    calls = 0

    def executor(scenario, recorder):
        nonlocal calls
        calls += 1
        return _executor(scenario, recorder)

    manifest = _manifest()
    manifest.cells[0].seeds.append(99)
    with pytest.raises(ExperimentConfigurationError, match="changed after freezing"):
        ExperimentRunner([
            CallableSubjectAdapter(_identity(), executor)
        ]).run(manifest)
    assert calls == 0

    drifted = _identity(version="9.9.9")
    with pytest.raises(ExperimentConfigurationError, match="identity mismatch"):
        ExperimentRunner([
            CallableSubjectAdapter(drifted, executor)
        ]).run(_manifest())
    assert calls == 0


def test_adapter_validates_oracles_before_external_execution():
    calls = 0

    def executor(scenario, recorder):
        nonlocal calls
        calls += 1
        return _executor(scenario, recorder)

    scenario = Scenario(
        scenario_id="invalid-oracle",
        query="fail before execution",
        oracles=[OracleSpec(kind="does_not_exist")],
    )

    with pytest.raises(ValueError, match="Unknown oracle kind"):
        CallableSubjectAdapter(_identity(), executor).run(scenario)

    assert calls == 0


def test_frozen_external_fault_campaign_classifies_and_scores_plans():
    identity = _identity()

    def adapter_factory(session):
        def executor(scenario, recorder):
            registry = DictToolRegistry()

            @registry.tool
            def echo(text: str):
                """Echo text."""
                return text

            tools = registry
            if session is not None:
                tools = FaultInjectingToolRegistry(tools, session)
            tools = RecordingToolRegistry(tools, recorder)
            result = tools.call("echo", {"text": "hello"})
            achieved = result == "hello"
            return RunResult(
                status=RunStatus.COMPLETED,
                stop_reason=StopReason.SUBJECT_COMPLETED,
                final_answer=str(result),
                evaluation=EvaluationResult(
                    achieved=achieved,
                    confidence=1.0,
                    explanation="exact result match",
                ),
            )

        return CallableSubjectAdapter(identity, executor)

    scenario = Scenario(
        scenario_id="external-faults",
        query="echo hello",
        oracles=[
            OracleSpec(
                kind=OracleKind.GOAL_ACHIEVEMENT,
                parameters={"expected": True},
            ),
            OracleSpec(
                kind=OracleKind.TOOL_RESULTS,
                parameters={"expected_last": {"echo": "hello"}},
            ),
        ],
    )
    plans = [
        FaultPlan(
            plan_id="stale",
            rules=[
                FaultRule(
                    rule_id="stale",
                    component=FaultComponent.TOOL,
                    action=FaultAction.TOOL_RESULT_STALE,
                    tool_name="echo",
                    replacement="old",
                )
            ],
        ),
        FaultPlan(
            plan_id="equivalent",
            rules=[
                FaultRule(
                    rule_id="same",
                    component=FaultComponent.TOOL,
                    action=FaultAction.TOOL_RESULT_STALE,
                    tool_name="echo",
                    replacement="hello",
                )
            ],
        ),
        FaultPlan(
            plan_id="not-applicable",
            rules=[
                FaultRule(
                    rule_id="missing",
                    component=FaultComponent.TOOL,
                    action=FaultAction.TOOL_RESULT_STALE,
                    tool_name="missing",
                    replacement="old",
                )
            ],
        ),
    ]
    frozen_at = datetime(2026, 7, 30, tzinfo=timezone.utc)
    digest = fault_experiment_manifest_digest(
        "external-fault-pilot",
        identity,
        scenario,
        plans,
        frozen_at=frozen_at,
    )
    manifest = FaultExperimentManifest(
        experiment_id="external-fault-pilot",
        subject=identity,
        scenario=scenario,
        plans=plans,
        frozen_at=frozen_at,
        manifest_digest=digest,
    )

    report = SubjectFaultCampaignRunner(adapter_factory).run(manifest)

    assert [item.status for item in report.campaign.outcomes] == [
        MutationStatus.KILLED,
        MutationStatus.EQUIVALENT,
        MutationStatus.NOT_APPLICABLE,
    ]
    assert report.campaign.killed == 1
    assert report.campaign.mutation_score == 1.0
    assert report.campaign.baseline.trace.metadata["execution_mode"] == (
        "subject_baseline"
    )
    assert report.campaign.outcomes[0].scenario_report.trace.metadata[
        "fault_plan_id"
    ] == "stale"
    restored = type(report).model_validate_json(report.model_dump_json())
    assert restored == report
