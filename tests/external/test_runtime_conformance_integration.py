import hashlib
import json
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

from research.pilots.runtime_conformance.cases import run_case_pair  # noqa: E402
from research.pilots.runtime_conformance.run import (  # noqa: E402
    RuntimeConformanceManifest,
)
from nanoharness.testing import RuntimeConformanceReport  # noqa: E402


@pytest.mark.parametrize("case_id", ["M1", "M2", "M3", "M4"])
def test_real_runtimes_match_m1_m4_semantic_projections(case_id):
    pair = run_case_pair(case_id)

    assert pair.comparison.passed is True
    assert pair.comparison.mismatches == []
    assert pair.nanoharness.report.model_validate_json(
        pair.nanoharness.report.model_dump_json()
    ) == pair.nanoharness.report
    assert pair.langgraph.report.model_validate_json(
        pair.langgraph.report.model_dump_json()
    ) == pair.langgraph.report


def test_m1_records_typed_tool_argument_and_delivered_observation():
    pair = run_case_pair("M1")

    assert pair.nanoharness.projection.tool_attempts[0].arguments == {"order_id": 7}
    assert pair.langgraph.projection.tool_attempts[0].arguments == {"order_id": 7}
    assert pair.nanoharness.projection.tool_attempts[0].result == {"status": "open"}
    assert pair.nanoharness.projection.tool_attempts[0].observation_delivered is True


def test_m2_records_recovery_after_transient_tool_error():
    pair = run_case_pair("M2")

    assert pair.nanoharness.projection.recovery_observed is True
    assert pair.langgraph.projection.recovery_observed is True
    assert [item.outcome for item in pair.nanoharness.projection.tool_attempts] == [
        "error",
        "success",
    ]


def test_m3_separates_normal_termination_from_goal_achievement():
    pair = run_case_pair("M3")

    assert pair.nanoharness.projection.run_status == "completed"
    assert pair.nanoharness.projection.goal_achieved is False
    assert pair.nanoharness.projection.report_passed is False
    assert pair.langgraph.projection.goal_achieved is False
    assert pair.langgraph.projection.report_passed is False


def test_m4_denies_write_before_underlying_tool_attempt():
    pair = run_case_pair("M4")

    for evidence in (pair.nanoharness, pair.langgraph):
        assert evidence.projection.goal_achieved is False
        assert evidence.projection.permission_decisions[0].allowed is False
        assert evidence.projection.tool_attempts == []
        assert evidence.report.passed is True


def test_archived_m1_m4_evidence_matches_manifest_and_checksums():
    root = (
        Path(__file__).parents[2]
        / "research"
        / "pilots"
        / "runtime_conformance"
    )
    manifest = RuntimeConformanceManifest.model_validate_json(
        (root / "manifest.json").read_text(encoding="utf-8")
    )
    report = RuntimeConformanceReport.model_validate_json(
        (root / "raw" / "conformance_report.json").read_text(encoding="utf-8")
    )
    summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))

    assert report.manifest_digest == manifest.manifest_digest
    assert report.artifact_revision == "cb61901d99ec5a0ee0c48e71408246221a846086"
    assert len(report.cells) == 8
    assert len(report.comparisons) == 4
    assert all(item.passed and not item.mismatches for item in report.comparisons)
    assert summary["passed"] is True
    assert summary["infrastructure_errors"] == []

    lines = (root / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    for line in lines:
        expected, relative = line.split("  ", 1)
        assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == expected
