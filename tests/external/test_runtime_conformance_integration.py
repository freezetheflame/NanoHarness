import pytest

pytest.importorskip("langgraph")

from research.pilots.runtime_conformance.cases import run_case_pair  # noqa: E402


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
