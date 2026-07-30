from typing import Any, Dict, List, Optional

import pytest

from nanoharness.components.context.simple_context import SimpleContextManager
from nanoharness.components.evaluator.trace_evaluator import TraceEvaluator
from nanoharness.components.hooks.simple_hooks import SimpleHookManager
from nanoharness.components.state.json_store import JsonStateStore
from nanoharness.components.tools.dict_registry import DictToolRegistry
from nanoharness.core.engine import NanoEngine
from nanoharness.core.schema import LLMResponse, ToolCall
from nanoharness.testing import (
    AgentTrace,
    InvalidTraceError,
    RecordedExecutionError,
    RecordingLLM,
    RecordingToolRegistry,
    ReplayExhaustedError,
    ReplayLLM,
    ReplayMismatchError,
    ReplaySession,
    ReplayToolRegistry,
    TraceEventType,
    TraceRecorder,
    TRACE_SCHEMA_VERSION,
    UnconsumedReplayEventsError,
    UnsupportedTraceVersionError,
)


def _engine(llm, tools, state_path):
    return NanoEngine(
        llm_client=llm,
        tools=tools,
        context=SimpleContextManager(system_prompt="Be deterministic."),
        state=JsonStateStore(state_path),
        hooks=SimpleHookManager(),
        evaluator=TraceEvaluator(),
    )


class TestEngineReplay:
    def test_recorded_run_replays_without_live_model_or_tools(self, mock_llm):
        recorder = TraceRecorder(trace_id="record-and-replay")
        registry = DictToolRegistry()
        live_calls = []

        @registry.tool
        def echo(text: str):
            """Echo text."""
            live_calls.append(text)
            return {"echoed": text}

        recording_llm = RecordingLLM(
            mock_llm([
                LLMResponse(
                    content="call echo",
                    tool_calls=[ToolCall(name="echo", arguments={"text": "hello"})],
                ),
                LLMResponse(content="done"),
            ]),
            recorder,
        )
        recording_tools = RecordingToolRegistry(registry, recorder)

        original = _engine(
            recording_llm,
            recording_tools,
            "/tmp/test_recording_engine.json",
        ).run("echo hello")

        assert live_calls == ["hello"]
        assert [event.event_type for event in recorder.events] == [
            TraceEventType.TOOL_SCHEMAS,
            TraceEventType.MODEL_EXCHANGE,
            TraceEventType.TOOL_EXCHANGE,
            TraceEventType.TOOL_SCHEMAS,
            TraceEventType.MODEL_EXCHANGE,
        ]

        session = ReplaySession(recorder.snapshot().model_dump_json())
        replay = _engine(
            ReplayLLM(session),
            ReplayToolRegistry(session),
            "/tmp/test_replay_engine.json",
        ).run("echo hello")

        session.assert_consumed()
        assert live_calls == ["hello"]
        assert replay.final_answer == original.final_answer
        assert replay.evaluation == original.evaluation
        assert replay["trajectory"] == original["trajectory"]

    def test_shared_session_checks_cross_component_order(self, mock_llm):
        recorder = TraceRecorder(trace_id="ordered")
        registry = DictToolRegistry()

        @registry.tool
        def noop():
            """No-op."""
            return "ok"

        RecordingToolRegistry(registry, recorder).get_tool_schemas()
        RecordingLLM(mock_llm([LLMResponse(content="done")]), recorder).chat([])
        session = ReplaySession(recorder.snapshot())

        with pytest.raises(ReplayMismatchError, match="Replay order mismatch"):
            ReplayLLM(session).chat([])


class TestReplayValidation:
    def test_model_request_mismatch_reports_expected_and_actual(self, mock_llm):
        recorder = TraceRecorder(trace_id="model-mismatch")
        RecordingLLM(mock_llm([LLMResponse(content="done")]), recorder).chat(
            [{"role": "user", "content": "original"}],
            tools=[],
        )
        replay = ReplayLLM(recorder.snapshot())

        with pytest.raises(ReplayMismatchError) as raised:
            replay.chat(
                [{"role": "user", "content": "changed"}],
                tools=[],
            )

        assert raised.value.expected["messages"][0]["content"] == "original"
        assert raised.value.actual["messages"][0]["content"] == "changed"

    def test_tool_call_mismatch_reports_arguments(self):
        recorder = TraceRecorder(trace_id="tool-mismatch")
        registry = DictToolRegistry()

        @registry.tool
        def echo(text: str):
            """Echo text."""
            return text

        RecordingToolRegistry(registry, recorder).call("echo", {"text": "original"})
        replay = ReplayToolRegistry(recorder.snapshot())

        with pytest.raises(ReplayMismatchError) as raised:
            replay.call("echo", {"text": "changed"})

        assert raised.value.expected["arguments"] == {"text": "original"}
        assert raised.value.actual["arguments"] == {"text": "changed"}

    def test_non_strict_replay_allows_changed_request(self, mock_llm):
        recorder = TraceRecorder(trace_id="non-strict")
        RecordingLLM(mock_llm([LLMResponse(content="recorded")]), recorder).chat(
            [{"role": "user", "content": "original"}],
        )

        response = ReplayLLM(recorder.snapshot(), strict=False).chat(
            [{"role": "user", "content": "changed"}],
        )

        assert response.content == "recorded"

    def test_redacted_requests_can_still_be_verified(self, mock_llm):
        recorder = TraceRecorder(trace_id="redacted")
        messages = [{"role": "user", "content": "call", "api_key": "secret"}]
        RecordingLLM(mock_llm([LLMResponse(content="done")]), recorder).chat(messages)

        response = ReplayLLM(recorder.snapshot()).chat(messages)

        assert response.content == "done"
        assert recorder.events[0].payload["messages"][0]["api_key"] == "[REDACTED]"


class TestReplayFailures:
    def test_recorded_model_exception_is_reproduced(self):
        class FailingLLM:
            def chat(
                self,
                messages: List[Dict[str, Any]],
                tools: Optional[List[Dict[str, Any]]] = None,
            ) -> LLMResponse:
                raise ValueError("provider unavailable")

        recorder = TraceRecorder(trace_id="model-error")
        with pytest.raises(ValueError, match="provider unavailable"):
            RecordingLLM(FailingLLM(), recorder).chat([])

        with pytest.raises(RecordedExecutionError) as raised:
            ReplayLLM(recorder.snapshot()).chat([])

        assert raised.value.component == "model"
        assert raised.value.error_type == "ValueError"
        assert raised.value.recorded_message == "provider unavailable"

    def test_recorded_tool_exception_is_reproduced(self):
        recorder = TraceRecorder(trace_id="tool-error")
        registry = DictToolRegistry()

        @registry.tool
        def fail():
            """Fail."""
            raise RuntimeError("tool unavailable")

        with pytest.raises(RuntimeError, match="tool unavailable"):
            RecordingToolRegistry(registry, recorder).call("fail", {})

        with pytest.raises(RecordedExecutionError) as raised:
            ReplayToolRegistry(recorder.snapshot()).call("fail", {})

        assert raised.value.component == "tool"
        assert raised.value.error_type == "RuntimeError"

    def test_unconsumed_and_exhausted_interactions_are_explicit(self, mock_llm):
        recorder = TraceRecorder(trace_id="bounds")
        RecordingLLM(mock_llm([LLMResponse(content="once")]), recorder).chat([])
        replay = ReplayLLM(recorder.snapshot())

        with pytest.raises(UnconsumedReplayEventsError):
            replay.assert_consumed()

        assert replay.chat([]).content == "once"
        replay.assert_consumed()

        with pytest.raises(ReplayExhaustedError):
            replay.chat([])

    def test_reset_replays_from_the_beginning(self, mock_llm):
        recorder = TraceRecorder(trace_id="reset")
        RecordingLLM(mock_llm([LLMResponse(content="again")]), recorder).chat([])
        replay = ReplayLLM(recorder.snapshot())

        assert replay.chat([]).content == "again"
        replay.reset()

        assert replay.chat([]).content == "again"


class TestReplayTraceIntegrity:
    def test_rejects_unsupported_trace_version(self):
        trace = AgentTrace(
            schema_version=TRACE_SCHEMA_VERSION + 1,
            trace_id="future",
            started_at=0,
        )

        with pytest.raises(
            UnsupportedTraceVersionError,
            match=f"version {TRACE_SCHEMA_VERSION + 1}",
        ):
            ReplaySession(trace)

    def test_legacy_v1_trace_is_migrated_without_mutating_source(self, mock_llm):
        recorder = TraceRecorder(trace_id="legacy")
        RecordingLLM(mock_llm([LLMResponse(content="old")]), recorder).chat([])
        data = recorder.snapshot().model_dump()
        data["schema_version"] = 1
        for event in data["events"]:
            event["schema_version"] = 1
        legacy = AgentTrace.model_validate(data)

        replay = ReplayLLM(legacy)

        assert replay.chat([]).content == "old"
        assert legacy.schema_version == 1

    @pytest.mark.parametrize(
        "mutation, message",
        [
            (lambda data: data["events"][0].update(trace_id="other"), "belongs to trace"),
            (lambda data: data["events"][1].update(event_id="event-0"), "Duplicate"),
            (lambda data: data["events"][1].update(sequence=0), "strictly increasing"),
        ],
    )
    def test_rejects_inconsistent_event_streams(self, mutation, message):
        recorder = TraceRecorder(
            trace_id="integrity",
            id_factory=iter(["event-0", "event-1"]).__next__,
        )
        recorder.record(TraceEventType.CUSTOM, {})
        recorder.record(TraceEventType.MODEL_EXCHANGE, {
            "messages": [],
            "tools": None,
            "response": LLMResponse(content="done"),
        })
        trace_data = recorder.snapshot().model_dump()
        mutation(trace_data)

        with pytest.raises(InvalidTraceError, match=message):
            ReplaySession(trace_data)
