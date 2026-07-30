import pytest

from nanoharness.core.schema import (
    EvaluationResult,
    RunResult,
    RunStatus,
    StopReason,
)
from nanoharness.testing import (
    LangGraphSubjectAdapter,
    OracleKind,
    OracleSpec,
    Scenario,
    SubjectIdentity,
    TraceEventType,
)


class FakeGraph:
    def __init__(self, snapshots):
        self.snapshots = snapshots
        self.calls = []

    def stream(self, input_state, **kwargs):
        self.calls.append((input_state, kwargs))
        yield from self.snapshots


def _identity():
    return SubjectIdentity(
        subject_id="langgraph@test-revision",
        runtime="langgraph",
        version="test",
        revision="test-revision",
        source_url="https://github.com/langchain-ai/langgraph",
        independently_developed=True,
    )


def _mapper(scenario, final_state, snapshots):
    achieved = final_state["answer"] == scenario.fixtures["expected"]
    return RunResult(
        status=RunStatus.COMPLETED,
        stop_reason=StopReason.MODEL_TERMINATED,
        final_answer=final_state["answer"],
        evaluation=EvaluationResult(
            achieved=achieved,
            confidence=1.0,
            explanation="exact deterministic match",
        ),
    )


def _scenario():
    return Scenario(
        scenario_id="langgraph-echo",
        query="hello",
        fixtures={"expected": "hello"},
        oracles=[
            OracleSpec(
                kind=OracleKind.GOAL_ACHIEVEMENT,
                parameters={"expected": True},
            ),
            OracleSpec(kind=OracleKind.LIFECYCLE),
        ],
    )


def test_langgraph_adapter_streams_values_from_fresh_graph_and_maps_result():
    graphs = []

    def factory():
        graph = FakeGraph([
            {"query": "hello"},
            {"query": "hello", "answer": "hello"},
        ])
        graphs.append(graph)
        return graph

    adapter = LangGraphSubjectAdapter(
        _identity(),
        factory,
        lambda scenario: {"query": scenario.query},
        _mapper,
        config_builder=lambda scenario: {"configurable": {"seed": scenario.seed}},
    )

    first = adapter.run(_scenario())
    second = adapter.run(_scenario())

    assert first.passed is True
    assert second.passed is True
    assert len(graphs) == 2
    assert graphs[0].calls == [
        (
            {"query": "hello"},
            {
                "config": {"configurable": {"seed": None}},
                "stream_mode": "values",
            },
        )
    ]
    custom = [
        event
        for event in first.trace.events
        if event.event_type is TraceEventType.CUSTOM
    ]
    assert [event.payload["snapshot_index"] for event in custom] == [0, 1]
    assert custom[-1].payload["state"]["answer"] == "hello"


def test_langgraph_adapter_normalizes_empty_stream_and_invalid_factory():
    empty = LangGraphSubjectAdapter(
        _identity(),
        lambda: FakeGraph([]),
        lambda scenario: {},
        _mapper,
    ).run(_scenario())
    invalid = LangGraphSubjectAdapter(
        _identity(),
        lambda: object(),
        lambda scenario: {},
        _mapper,
    ).run(_scenario())

    assert empty.execution_error.error_type == "RuntimeError"
    assert invalid.execution_error.error_type == "TypeError"
    assert empty.passed is False
    assert invalid.passed is False


def test_langgraph_adapter_rejects_identity_for_another_runtime():
    identity = _identity().model_copy(update={"runtime": "other"})

    with pytest.raises(ValueError, match="runtime must be 'langgraph'"):
        LangGraphSubjectAdapter(
            identity,
            lambda: FakeGraph([]),
            lambda scenario: {},
            _mapper,
        )


def test_recorder_aware_builder_receives_scenario_and_active_recorder():
    captured = []

    def builder(scenario, recorder):
        captured.append((scenario.scenario_id, recorder.trace_id))
        recorder.record(
            TraceEventType.CUSTOM,
            {"adapter_event": "builder_created"},
        )
        return FakeGraph([{"answer": "hello"}])

    adapter = LangGraphSubjectAdapter(
        _identity(),
        None,
        lambda scenario: {"query": scenario.query},
        _mapper,
        graph_builder=builder,
    )

    report = adapter.run(_scenario())

    assert report.passed is True
    assert captured == [(_scenario().scenario_id, report.trace.trace_id)]
    assert any(
        event.payload.get("adapter_event") == "builder_created"
        for event in report.trace.events
    )


def test_langgraph_adapter_requires_exactly_one_graph_construction_strategy():
    with pytest.raises(ValueError, match="exactly one"):
        LangGraphSubjectAdapter(
            _identity(),
            None,
            lambda scenario: {},
            _mapper,
        )
    with pytest.raises(ValueError, match="exactly one"):
        LangGraphSubjectAdapter(
            _identity(),
            lambda: FakeGraph([]),
            lambda scenario: {},
            _mapper,
            graph_builder=lambda scenario, recorder: FakeGraph([]),
        )
