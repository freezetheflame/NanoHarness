import json
from concurrent.futures import ThreadPoolExecutor
from itertools import count

from nanoharness.components.context.simple_context import SimpleContextManager
from nanoharness.components.evaluator.trace_evaluator import TraceEvaluator
from nanoharness.components.hooks.simple_hooks import SimpleHookManager
from nanoharness.components.state.json_store import JsonStateStore
from nanoharness.components.tools.dict_registry import DictToolRegistry
from nanoharness.core.engine import NanoEngine
from nanoharness.core.schema import LLMResponse, ToolCall
from nanoharness.testing import (
    AgentTrace,
    TraceEventType,
    TraceRecorder,
    redact_sensitive_fields,
)


def _deterministic_recorder(**kwargs):
    identifiers = count()
    timestamps = count(100)
    return TraceRecorder(
        id_factory=lambda: f"id-{next(identifiers)}",
        clock=lambda: float(next(timestamps)),
        **kwargs,
    )


class TestTraceRecorder:
    def test_records_ordered_detached_events(self):
        recorder = _deterministic_recorder(trace_id="trace-1")

        event = recorder.record(TraceEventType.CUSTOM, {"value": 1})
        event.payload["value"] = 99
        recorder.record(TraceEventType.STATE_SAVED, {"step": 0})

        assert [item.sequence for item in recorder.events] == [0, 1]
        assert recorder.events[0].payload == {"value": 1}
        assert all(item.trace_id == "trace-1" for item in recorder.events)

    def test_snapshot_round_trips_through_json(self):
        recorder = _deterministic_recorder(trace_id="trace-json")
        recorder.record(TraceEventType.CUSTOM, {"nested": [1, 2]})

        encoded = recorder.snapshot().model_dump_json()
        restored = AgentTrace.model_validate_json(encoded)

        assert json.loads(encoded)["schema_version"] == 1
        assert restored == recorder.snapshot()

    def test_redacts_nested_sensitive_fields(self):
        recorder = _deterministic_recorder(trace_id="trace-secret")

        recorder.record(
            TraceEventType.TOOL_STARTED,
            {
                "arguments": {
                    "api_key": "secret-key",
                    "nested": {"password": "secret-password", "path": "/tmp"},
                }
            },
        )

        arguments = recorder.events[0].payload["arguments"]
        assert arguments["api_key"] == "[REDACTED]"
        assert arguments["nested"]["password"] == "[REDACTED]"
        assert arguments["nested"]["path"] == "/tmp"

    def test_supports_custom_redactor(self):
        recorder = _deterministic_recorder(
            trace_id="trace-custom-redactor",
            redactor=lambda value: redact_sensitive_fields(
                value,
                sensitive_fields={"private"},
            ),
        )

        recorder.record(TraceEventType.CUSTOM, {"private": "hide", "api_key": "keep"})

        assert recorder.events[0].payload == {
            "private": "[REDACTED]",
            "api_key": "keep",
        }

    def test_attach_is_idempotent(self):
        hooks = SimpleHookManager()
        recorder = _deterministic_recorder(trace_id="trace-hooks")

        recorder.attach(hooks)
        recorder.attach(hooks)
        hooks.trigger("on_task_start", "hello")

        assert len(recorder.events) == 1

    def test_reset_starts_new_trace(self):
        recorder = _deterministic_recorder(trace_id="first", metadata={"suite": "unit"})
        recorder.record(TraceEventType.CUSTOM, {})

        recorder.reset(trace_id="second")

        assert recorder.trace_id == "second"
        assert recorder.events == []
        assert recorder.snapshot().metadata == {}

    def test_concurrent_records_receive_unique_ordered_sequences(self):
        recorder = TraceRecorder(trace_id="concurrent")

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(
                pool.map(
                    lambda value: recorder.record(
                        TraceEventType.CUSTOM,
                        {"value": value},
                    ),
                    range(100),
                )
            )

        assert len(recorder.events) == 100
        assert [event.sequence for event in recorder.events] == list(range(100))
        assert len({event.event_id for event in recorder.events}) == 100


class TestTraceRecorderEngineIntegration:
    def test_records_standard_engine_lifecycle(self, mock_llm):
        hooks = SimpleHookManager()
        recorder = _deterministic_recorder(trace_id="engine-trace")
        recorder.attach(hooks)
        engine = NanoEngine(
            llm_client=mock_llm([LLMResponse(content="done")]),
            tools=DictToolRegistry(),
            context=SimpleContextManager(),
            state=JsonStateStore("/tmp/test_trace_recorder.json"),
            hooks=hooks,
            evaluator=TraceEvaluator(),
        )

        result = engine.run("hello")

        assert [event.event_type for event in recorder.events] == [
            TraceEventType.TASK_STARTED,
            TraceEventType.MODEL_RESPONSE,
            TraceEventType.STEP_COMPLETED,
            TraceEventType.TASK_COMPLETED,
        ]
        assert recorder.events[0].payload == {"data": "hello"}
        assert recorder.events[-1].payload["status"] == "completed"
        assert recorder.events[-1].payload["evaluation"]["achieved"] is True
        assert result.success is True

    def test_tool_arguments_are_captured_from_model_response(self, mock_llm):
        hooks = SimpleHookManager()
        recorder = _deterministic_recorder(trace_id="tool-trace")
        recorder.attach(hooks)
        registry = DictToolRegistry()

        @registry.tool
        def echo(text: str):
            """Echo text."""
            return text

        engine = NanoEngine(
            llm_client=mock_llm([
                LLMResponse(
                    content="call echo",
                    tool_calls=[ToolCall(name="echo", arguments={"text": "hello"})],
                ),
                LLMResponse(content="done"),
            ]),
            tools=registry,
            context=SimpleContextManager(),
            state=JsonStateStore("/tmp/test_trace_tool.json"),
            hooks=hooks,
            evaluator=TraceEvaluator(),
        )

        engine.run("echo")

        model_event = recorder.events[1]
        assert model_event.payload["tool_calls"][0] == {
            "name": "echo",
            "arguments": {"text": "hello"},
        }
