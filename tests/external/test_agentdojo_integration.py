from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

pytest.importorskip("agentdojo.task_suite.load_suites")

from agentdojo.agent_pipeline.ground_truth_pipeline import (  # noqa: E402
    GroundTruthPipeline,
)
from nanoharness.testing import (  # noqa: E402
    AGENTDOJO_PACKAGE_VERSION,
    AGENTDOJO_REVISION,
    AgentDojoBenchmarkAdapter,
    AgentDojoFaultAdapterFactory,
    AgentDojoSubjectAdapter,
    BenchmarkConfigurationError,
    BenchmarkManifest,
    ExperimentManifest,
    ExperimentReport,
    FaultAction,
    FaultComponent,
    FaultExperimentManifest,
    FaultPlan,
    FaultRule,
    MutationStatus,
    SubjectFaultCampaignRunner,
    SubjectIdentity,
    TraceEventType,
    fault_experiment_manifest_digest,
)


def test_pinned_agentdojo_tasks_convert_with_real_default_environments():
    adapter = AgentDojoBenchmarkAdapter.from_installed()

    workspace = adapter.convert_user_tasks(
        "workspace",
        ["user_task_0", "user_task_16"],
        frozen_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
    )
    banking = adapter.convert_user_tasks(
        "banking",
        ["user_task_1", "user_task_6"],
        frozen_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
    )

    assert adapter.source.package_version == AGENTDOJO_PACKAGE_VERSION
    assert adapter.source.revision == AGENTDOJO_REVISION
    assert len(workspace.tasks) == len(banking.tasks) == 2
    for manifest in (workspace, banking):
        assert all(task.reference_tool_calls for task in manifest.tasks)
        assert all(not task.scenario.oracles for task in manifest.tasks)
        manifest.assert_unchanged()
        assert BenchmarkManifest.model_validate_json(
            manifest.model_dump_json()
        ) == manifest

    with pytest.raises(BenchmarkConfigurationError, match="does not match"):
        AgentDojoBenchmarkAdapter.from_installed(
            expected_package_version="0.0.0"
        )


def test_archived_agentdojo_conversion_matches_frozen_manifests_and_checksums():
    root = (
        Path(__file__).parents[2]
        / "research"
        / "pilots"
        / "agentdojo_offline_conversion"
        / "raw"
    )
    expected_checksums = {
        "banking_manifest.json": (
            "bdf616930bd3d9bc2ff6fe04433edf26dcbc6899f8a76a9142944f5bdf4ba69f"
        ),
        "workspace_manifest.json": (
            "c487fc22b94f2e67020d7058f4881d169e1a11d65f4b0a89879e554c84156a3a"
        ),
        "conversion_summary.json": (
            "c0306e346a9f558561d77b8ce79eee09849aaf0c055f13d39b7f2dccef8bc828"
        ),
    }

    manifests = []
    for filename in ("workspace_manifest.json", "banking_manifest.json"):
        raw = (root / filename).read_bytes()
        manifest = BenchmarkManifest.model_validate_json(raw)
        manifest.assert_unchanged()
        manifests.append(manifest)
        assert hashlib.sha256(raw).hexdigest() == expected_checksums[filename]
    summary_raw = (root / "conversion_summary.json").read_bytes()
    summary = json.loads(summary_raw)

    assert sum(len(manifest.tasks) for manifest in manifests) == 4
    assert all(manifest.oracle_binding == "unbound" for manifest in manifests)
    assert summary["live_model"] is False
    assert summary["executes_agentdojo_runtime"] is False
    assert summary["reproduces_agentdojo_results"] is False
    assert hashlib.sha256(summary_raw).hexdigest() == expected_checksums[
        "conversion_summary.json"
    ]


def test_real_agentdojo_ground_truth_pipeline_uses_original_utility_scorer():
    root = (
        Path(__file__).parents[2]
        / "research"
        / "pilots"
        / "agentdojo_offline_conversion"
        / "raw"
    )
    manifest = BenchmarkManifest.model_validate_json(
        (root / "banking_manifest.json").read_text()
    )
    identity = SubjectIdentity(
        subject_id="agentdojo-ground-truth@0.1.35",
        runtime="agentdojo",
        version="0.1.35",
        revision=manifest.source.revision,
        source_url=manifest.source.source_url,
        independently_developed=True,
        metadata={"pipeline": "GroundTruthPipeline"},
    )
    adapter = AgentDojoSubjectAdapter.from_installed(
        identity,
        manifest,
        lambda scenario, task: GroundTruthPipeline(task),
    )

    reports = [
        adapter.run(adapter.scenario_for(record.source_task_id, seed=0))
        for record in manifest.tasks
    ]

    assert all(report.passed for report in reports)
    assert all(report.result.evaluation.achieved for report in reports)
    assert all(
        any(
            event.event_type is TraceEventType.TOOL_STARTED
            for event in report.trace.events
        )
        for report in reports
    )
    assert all(
        any(
            event.payload.get("adapter_event") == "utility_scored"
            and event.payload.get("scorer_path") == "utility"
            for event in report.trace.events
        )
        for report in reports
    )


def test_archived_agentdojo_scorer_bridge_preserves_original_utility_evidence():
    root = (
        Path(__file__).parents[2]
        / "research"
        / "pilots"
        / "agentdojo_scorer_bridge"
    )
    manifest_raw = (root / "manifest.json").read_bytes()
    report_raw = (root / "raw" / "experiment_report.json").read_bytes()
    manifest = ExperimentManifest.model_validate_json(manifest_raw)
    report = ExperimentReport.model_validate_json(report_raw)

    assert report.manifest == manifest
    assert manifest.manifest_digest == (
        "a7d5ad3025193c09ee8283774bbf04a9a01430495ac521a29d107f110d7d7b9f"
    )
    assert len(report.observations) == 4
    assert all(observation.report.passed for observation in report.observations)
    assert all(
        observation.report.result.evaluation.achieved
        for observation in report.observations
    )
    assert all(
        observation.report.trace.metadata["benchmark"]["oracle_binding"]
        == "agentdojo_user_utility"
        for observation in report.observations
    )
    for observation in report.observations:
        scored = [
            event
            for event in observation.report.trace.events
            if event.payload.get("adapter_event") == "utility_scored"
        ]
        assert len(scored) == 1
        assert scored[0].payload["scorer_path"] == "utility"
        assert scored[0].payload["scorer_trace_scope"] == "final_attempt"
        assert scored[0].payload["pre_environment_digest"]
        assert scored[0].payload["post_environment_digest"]
    assert hashlib.sha256(manifest_raw).hexdigest() == (
        "a857f6f13eab6b1dae3ff1dd3d9d5e96e4e48809a4de16309755cc135cf44bd1"
    )
    assert hashlib.sha256(report_raw).hexdigest() == (
        "6a2e9fe14941ae7cdd87a6e67f06db0428525469da612f6027897a05de2424ab"
    )


def test_real_agentdojo_original_utility_classifies_native_tool_faults():
    root = (
        Path(__file__).parents[2]
        / "research"
        / "pilots"
        / "agentdojo_offline_conversion"
        / "raw"
    )
    benchmark = BenchmarkManifest.model_validate_json(
        (root / "banking_manifest.json").read_text()
    )
    identity = SubjectIdentity(
        subject_id="agentdojo-ground-truth-banking@0.1.35",
        runtime="agentdojo",
        version="0.1.35",
        revision=benchmark.source.revision,
        source_url=benchmark.source.source_url,
        independently_developed=True,
        metadata={"pipeline": "GroundTruthPipeline", "suite": "banking"},
    )
    factory = AgentDojoFaultAdapterFactory(
        identity,
        benchmark,
        lambda scenario, task: GroundTruthPipeline(task),
    )
    plans = [
        FaultPlan(
            plan_id="drop-recurring",
            rules=[
                FaultRule(
                    rule_id="drop-recurring-argument",
                    component=FaultComponent.TOOL,
                    action=FaultAction.TOOL_ARGUMENT_DROP,
                    tool_name="schedule_transaction",
                    argument="recurring",
                )
            ],
        ),
        FaultPlan(
            plan_id="stale-transaction-query",
            rules=[
                FaultRule(
                    rule_id="stale-transactions",
                    component=FaultComponent.TOOL,
                    action=FaultAction.TOOL_RESULT_STALE,
                    tool_name="get_most_recent_transactions",
                    replacement=[],
                )
            ],
        ),
        FaultPlan(
            plan_id="duplicate-schedule",
            rules=[
                FaultRule(
                    rule_id="duplicate-schedule-call",
                    component=FaultComponent.TOOL,
                    action=FaultAction.TOOL_CALL_DUPLICATE,
                    tool_name="schedule_transaction",
                )
            ],
        ),
    ]
    scenario = factory.scenario_for("user_task_6")
    frozen_at = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
    experiment_id = "agentdojo-native-tool-fault-test"
    digest = fault_experiment_manifest_digest(
        experiment_id,
        identity,
        scenario,
        plans,
        frozen_at=frozen_at,
    )
    manifest = FaultExperimentManifest(
        experiment_id=experiment_id,
        subject=identity,
        scenario=scenario,
        plans=plans,
        frozen_at=frozen_at,
        manifest_digest=digest,
    )

    report = SubjectFaultCampaignRunner(factory).run(manifest)

    assert report.campaign.baseline.passed is True
    assert [outcome.status for outcome in report.campaign.outcomes] == [
        MutationStatus.KILLED,
        MutationStatus.SURVIVED,
        MutationStatus.SURVIVED,
    ]
    assert report.campaign.killed == 1
    assert report.campaign.survived == 2
    assert report.campaign.mutation_score == pytest.approx(1 / 3)
    assert all(
        outcome.fault_report.effective_rule_ids
        for outcome in report.campaign.outcomes
    )
    stale = report.campaign.outcomes[1].scenario_report
    stale_exchange = next(
        event
        for event in stale.trace.events
        if event.event_type is TraceEventType.TOOL_EXCHANGE
        and event.payload.get("name") == "get_most_recent_transactions"
    )
    assert stale_exchange.payload["result"] == "[]"
    duplicate = report.campaign.outcomes[2].scenario_report
    duplicate_exchange = next(
        event
        for event in duplicate.trace.events
        if event.event_type is TraceEventType.TOOL_EXCHANGE
        and event.payload.get("name") == "schedule_transaction"
    )
    assert duplicate_exchange.payload["attempt_count"] == 2
