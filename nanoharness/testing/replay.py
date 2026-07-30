"""Deterministic recording and replay adapters for model and tool boundaries."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from typing import Any, Dict, List, Optional, Set, Union

from nanoharness.core.base import BaseToolRegistry, LLMProtocol
from nanoharness.core.schema import LLMResponse
from nanoharness.testing.trace import (
    TRACE_SCHEMA_VERSION,
    AgentTrace,
    TraceEvent,
    TraceEventType,
    TraceRecorder,
    normalize_trace_value,
    redact_sensitive_fields,
    upgrade_trace,
)


_REPLAY_EVENT_TYPES = {
    TraceEventType.MODEL_EXCHANGE,
    TraceEventType.MODEL_ERROR,
    TraceEventType.TOOL_SCHEMAS,
    TraceEventType.TOOL_EXCHANGE,
    TraceEventType.TOOL_ERROR,
}


class ReplayError(RuntimeError):
    """Base class for deterministic replay failures."""


class ReplayExhaustedError(ReplayError):
    """Raised when execution requests more recorded interactions than exist."""


class InvalidTraceError(ReplayError):
    """Raised when trace identity, ordering, or event IDs are inconsistent."""


class UnsupportedTraceVersionError(ReplayError):
    """Raised when replay has no migration path for a trace schema version."""


class ReplayMismatchError(ReplayError):
    """Raised when a replayed call differs from the recorded interaction."""

    def __init__(
        self,
        message: str,
        *,
        sequence: int,
        expected: Any,
        actual: Any,
    ):
        super().__init__(message)
        self.sequence = sequence
        self.expected = expected
        self.actual = actual


class UnconsumedReplayEventsError(ReplayError):
    """Raised when a replay finishes before consuming all recorded I/O."""


class RecordedExecutionError(ReplayError):
    """Deterministic representation of an exception observed while recording."""

    def __init__(self, component: str, error_type: str, message: str):
        super().__init__(f"Recorded {component} error {error_type}: {message}")
        self.component = component
        self.error_type = error_type
        self.recorded_message = message


def _detached_trace(trace: Union[AgentTrace, Dict[str, Any], str]) -> AgentTrace:
    if isinstance(trace, AgentTrace):
        detached = AgentTrace.model_validate(trace.model_dump())
    elif isinstance(trace, str):
        detached = AgentTrace.model_validate_json(trace)
    else:
        detached = AgentTrace.model_validate(trace)
    try:
        return upgrade_trace(detached)
    except ValueError as exc:
        raise UnsupportedTraceVersionError(str(exc)) from exc


def _prepare(value: Any, redactor: Callable[[Any], Any]) -> Any:
    return redactor(normalize_trace_value(value))


class ReplaySession:
    """Shared ordered cursor for replaying all model and tool interactions.

    A single session should be shared by ``ReplayLLM`` and
    ``ReplayToolRegistry`` so cross-component ordering is checked as well as
    each individual request.
    """

    def __init__(self, trace: Union[AgentTrace, Dict[str, Any], str]):
        detached = _detached_trace(trace)
        self._validate_trace(detached)
        self.trace_id = detached.trace_id
        self._events = [
            event
            for event in detached.events
            if event.event_type in _REPLAY_EVENT_TYPES
        ]
        self._index = 0
        self._lock = threading.RLock()

    @staticmethod
    def _validate_trace(trace: AgentTrace) -> None:
        if trace.schema_version != TRACE_SCHEMA_VERSION:
            raise UnsupportedTraceVersionError(
                f"Trace schema version {trace.schema_version} is unsupported; "
                f"expected {TRACE_SCHEMA_VERSION}"
            )
        previous_sequence = -1
        event_ids = set()
        for event in trace.events:
            if event.schema_version != TRACE_SCHEMA_VERSION:
                raise UnsupportedTraceVersionError(
                    f"Event {event.event_id!r} uses schema version "
                    f"{event.schema_version}; expected {TRACE_SCHEMA_VERSION}"
                )
            if event.trace_id != trace.trace_id:
                raise InvalidTraceError(
                    f"Event {event.event_id!r} belongs to trace "
                    f"{event.trace_id!r}, not {trace.trace_id!r}"
                )
            if event.event_id in event_ids:
                raise InvalidTraceError(f"Duplicate event ID {event.event_id!r}")
            if event.sequence <= previous_sequence:
                raise InvalidTraceError(
                    f"Trace sequences must be strictly increasing; got "
                    f"{event.sequence} after {previous_sequence}"
                )
            event_ids.add(event.event_id)
            previous_sequence = event.sequence

    @property
    def consumed(self) -> int:
        with self._lock:
            return self._index

    @property
    def remaining(self) -> int:
        with self._lock:
            return len(self._events) - self._index

    def consume(
        self,
        expected_types: Iterable[TraceEventType],
        *,
        component: str,
    ) -> TraceEvent:
        allowed: Set[TraceEventType] = set(expected_types)
        with self._lock:
            if self._index >= len(self._events):
                names = ", ".join(sorted(item.value for item in allowed))
                raise ReplayExhaustedError(
                    f"Replay trace {self.trace_id!r} has no event left for "
                    f"{component}; expected one of: {names}"
                )
            event = self._events[self._index]
            if event.event_type not in allowed:
                names = ", ".join(sorted(item.value for item in allowed))
                raise ReplayMismatchError(
                    f"Replay order mismatch for {component} at trace sequence "
                    f"{event.sequence}: expected one of {names}, got "
                    f"{event.event_type.value}",
                    sequence=event.sequence,
                    expected=names,
                    actual=event.event_type.value,
                )
            self._index += 1
            return TraceEvent.model_validate(event.model_dump())

    def assert_consumed(self) -> None:
        if self.remaining:
            with self._lock:
                next_event = self._events[self._index]
            raise UnconsumedReplayEventsError(
                f"Replay trace {self.trace_id!r} has {self.remaining} unconsumed "
                f"interaction(s); next is {next_event.event_type.value} at "
                f"sequence {next_event.sequence}"
            )

    def reset(self) -> None:
        with self._lock:
            self._index = 0


class RecordingLLM:
    """LLM adapter that records normalized requests, responses, and failures."""

    def __init__(
        self,
        delegate: LLMProtocol,
        recorder: TraceRecorder,
        *,
        clock: Callable[[], float] = time.perf_counter,
    ):
        self._delegate = delegate
        self._recorder = recorder
        self._clock = clock

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        started_at = self._clock()
        try:
            response = self._delegate.chat(messages, tools=tools)
        except Exception as exc:
            self._recorder.record(
                TraceEventType.MODEL_ERROR,
                {
                    "messages": messages,
                    "tools": tools,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "duration_ms": (self._clock() - started_at) * 1000,
                },
            )
            raise
        self._recorder.record(
            TraceEventType.MODEL_EXCHANGE,
            {
                "messages": messages,
                "tools": tools,
                "response": response,
                "duration_ms": (self._clock() - started_at) * 1000,
            },
        )
        return response


class ReplayLLM:
    """LLM protocol implementation backed by recorded model exchanges."""

    def __init__(
        self,
        trace_or_session: Union[AgentTrace, Dict[str, Any], str, ReplaySession],
        *,
        strict: bool = True,
        redactor: Callable[[Any], Any] = redact_sensitive_fields,
    ):
        self.session = (
            trace_or_session
            if isinstance(trace_or_session, ReplaySession)
            else ReplaySession(trace_or_session)
        )
        self.strict = strict
        self._redactor = redactor

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        event = self.session.consume(
            {TraceEventType.MODEL_EXCHANGE, TraceEventType.MODEL_ERROR},
            component="model",
        )
        expected_request = {
            "messages": event.payload.get("messages"),
            "tools": event.payload.get("tools"),
        }
        actual_request = _prepare(
            {"messages": messages, "tools": tools},
            self._redactor,
        )
        if self.strict and actual_request != expected_request:
            raise ReplayMismatchError(
                f"Model request mismatch at trace sequence {event.sequence}",
                sequence=event.sequence,
                expected=expected_request,
                actual=actual_request,
            )
        if event.event_type is TraceEventType.MODEL_ERROR:
            raise RecordedExecutionError(
                "model",
                str(event.payload.get("error_type", "Exception")),
                str(event.payload.get("error_message", "")),
            )
        return LLMResponse.model_validate(event.payload["response"])

    def assert_consumed(self) -> None:
        self.session.assert_consumed()

    def reset(self) -> None:
        self.session.reset()


class RecordingToolRegistry(BaseToolRegistry):
    """Tool registry decorator that records schemas, results, and failures."""

    def __init__(
        self,
        delegate: BaseToolRegistry,
        recorder: TraceRecorder,
        *,
        clock: Callable[[], float] = time.perf_counter,
    ):
        self._delegate = delegate
        self._recorder = recorder
        self._clock = clock

    def get_tool_schemas(self) -> List[Dict]:
        schemas = self._delegate.get_tool_schemas()
        self._recorder.record(TraceEventType.TOOL_SCHEMAS, {"schemas": schemas})
        return schemas

    def call(self, name: str, args: Dict) -> Any:
        started_at = self._clock()
        try:
            result = self._delegate.call(name, args)
        except Exception as exc:
            self._recorder.record(
                TraceEventType.TOOL_ERROR,
                {
                    "name": name,
                    "arguments": args,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "duration_ms": (self._clock() - started_at) * 1000,
                },
            )
            raise
        self._recorder.record(
            TraceEventType.TOOL_EXCHANGE,
            {
                "name": name,
                "arguments": args,
                "result": result,
                "duration_ms": (self._clock() - started_at) * 1000,
            },
        )
        return result

    def reset(self) -> None:
        self._delegate.reset()


class ReplayToolRegistry(BaseToolRegistry):
    """Tool registry implementation backed by recorded tool interactions."""

    def __init__(
        self,
        trace_or_session: Union[AgentTrace, Dict[str, Any], str, ReplaySession],
        *,
        strict: bool = True,
        redactor: Callable[[Any], Any] = redact_sensitive_fields,
    ):
        self.session = (
            trace_or_session
            if isinstance(trace_or_session, ReplaySession)
            else ReplaySession(trace_or_session)
        )
        self.strict = strict
        self._redactor = redactor

    def get_tool_schemas(self) -> List[Dict]:
        event = self.session.consume(
            {TraceEventType.TOOL_SCHEMAS},
            component="tool schemas",
        )
        return list(event.payload["schemas"])

    def call(self, name: str, args: Dict) -> Any:
        event = self.session.consume(
            {TraceEventType.TOOL_EXCHANGE, TraceEventType.TOOL_ERROR},
            component="tool",
        )
        expected_call = {
            "name": event.payload.get("name"),
            "arguments": event.payload.get("arguments"),
        }
        actual_call = _prepare(
            {"name": name, "arguments": args},
            self._redactor,
        )
        if self.strict and actual_call != expected_call:
            raise ReplayMismatchError(
                f"Tool call mismatch at trace sequence {event.sequence}",
                sequence=event.sequence,
                expected=expected_call,
                actual=actual_call,
            )
        if event.event_type is TraceEventType.TOOL_ERROR:
            raise RecordedExecutionError(
                "tool",
                str(event.payload.get("error_type", "Exception")),
                str(event.payload.get("error_message", "")),
            )
        return event.payload.get("result")

    def assert_consumed(self) -> None:
        self.session.assert_consumed()

    def reset(self) -> None:
        self.session.reset()
