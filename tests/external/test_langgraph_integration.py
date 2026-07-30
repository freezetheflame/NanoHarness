import hashlib
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

from research.pilots.langgraph_deterministic.run import (  # noqa: E402
    build_graph,
    build_input,
    map_result,
)
from nanoharness.core.schema import StopReason  # noqa: E402
from nanoharness.testing import (  # noqa: E402
    ExperimentManifest,
    ExperimentReport,
    FaultExperimentManifest,
    LangGraphSubjectAdapter,
    OracleKind,
    OracleSpec,
    Scenario,
    SubjectFaultExperimentReport,
    TraceEventType,
)


def test_installed_langgraph_executes_real_compiled_state_graph():
    adapter = LangGraphSubjectAdapter.from_installed(
        build_graph,
        build_input,
        map_result,
    )
    scenario = Scenario(
        scenario_id="real-langgraph",
        query="  Hello Agent  ",
        fixtures={"expected": "echo:hello agent"},
        oracles=[
            OracleSpec(kind=OracleKind.GOAL_ACHIEVEMENT),
            OracleSpec(kind=OracleKind.LIFECYCLE),
        ],
    )

    report = adapter.run(scenario)

    assert report.passed is True
    assert report.result.stop_reason is StopReason.SUBJECT_COMPLETED
    assert report.result.final_answer == "echo:hello agent"
    assert adapter.identity.version == "1.2.10"


def test_archived_langgraph_pilot_matches_frozen_manifest_and_checksum():
    root = (
        Path(__file__).parents[2]
        / "research"
        / "pilots"
        / "langgraph_deterministic"
    )
    manifest = ExperimentManifest.model_validate_json(
        (root / "manifest.json").read_text()
    )
    raw_bytes = (root / "raw" / "pilot_report.json").read_bytes()
    report = ExperimentReport.model_validate_json(raw_bytes)

    assert report.manifest == manifest
    assert len(report.observations) == 6
    assert all(observation.report.passed for observation in report.observations)
    assert hashlib.sha256(raw_bytes).hexdigest() == (
        "01e44e7a621bf95a7798a72fc59f2e56d7ce99e2a3c36b143cd74e905ece4094"
    )


def test_archived_langgraph_tool_fault_campaign_has_attempt_evidence():
    root = (
        Path(__file__).parents[2]
        / "research"
        / "pilots"
        / "langgraph_tool_faults"
    )
    manifest = FaultExperimentManifest.model_validate_json(
        (root / "manifest.json").read_text()
    )
    raw_bytes = (root / "raw" / "fault_campaign.json").read_bytes()
    report = SubjectFaultExperimentReport.model_validate_json(raw_bytes)

    assert report.manifest == manifest
    assert report.campaign.baseline.passed is True
    assert report.campaign.killed == 3
    assert report.campaign.survived == 0
    assert report.campaign.mutation_score == 1.0
    duplicate = report.campaign.outcomes[1].scenario_report
    exchanges = [
        event
        for event in duplicate.trace.events
        if event.event_type is TraceEventType.TOOL_EXCHANGE
    ]
    assert exchanges[-1].payload["attempt_count"] == 2
    assert hashlib.sha256(raw_bytes).hexdigest() == (
        "854add94ab1d9f2e7bcd765f72f94d4b58d63e2f9f3ddb1b2c524b2f740798c1"
    )
