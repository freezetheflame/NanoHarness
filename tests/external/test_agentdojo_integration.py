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
    AgentDojoSubjectAdapter,
    BenchmarkConfigurationError,
    BenchmarkManifest,
    SubjectIdentity,
    TraceEventType,
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
