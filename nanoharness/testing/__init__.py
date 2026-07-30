"""Testing components for recording, replaying, and validating agent runs."""

from nanoharness.testing.trace import (
    TRACE_SCHEMA_VERSION,
    AgentTrace,
    TraceEvent,
    TraceEventType,
    TraceRecorder,
    normalize_trace_value,
    redact_sensitive_fields,
)
from nanoharness.testing.replay import (
    InvalidTraceError,
    RecordedExecutionError,
    RecordingLLM,
    RecordingToolRegistry,
    ReplayError,
    ReplayExhaustedError,
    ReplayLLM,
    ReplayMismatchError,
    ReplaySession,
    ReplayToolRegistry,
    UnconsumedReplayEventsError,
    UnsupportedTraceVersionError,
)

__all__ = [
    "TRACE_SCHEMA_VERSION",
    "AgentTrace",
    "TraceEvent",
    "TraceEventType",
    "TraceRecorder",
    "normalize_trace_value",
    "redact_sensitive_fields",
    "RecordedExecutionError",
    "InvalidTraceError",
    "RecordingLLM",
    "RecordingToolRegistry",
    "ReplayError",
    "ReplayExhaustedError",
    "ReplayLLM",
    "ReplayMismatchError",
    "ReplaySession",
    "ReplayToolRegistry",
    "UnconsumedReplayEventsError",
    "UnsupportedTraceVersionError",
]
