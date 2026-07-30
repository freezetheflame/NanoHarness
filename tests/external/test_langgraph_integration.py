import pytest

pytest.importorskip("langgraph")

from research.pilots.langgraph_deterministic.run import (  # noqa: E402
    build_graph,
    build_input,
    map_result,
)
from nanoharness.core.schema import StopReason  # noqa: E402
from nanoharness.testing import (  # noqa: E402
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
