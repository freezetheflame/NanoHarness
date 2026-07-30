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
    LangGraphSubjectAdapter,
    OracleKind,
    OracleSpec,
    Scenario,
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
