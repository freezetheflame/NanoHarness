"""Versioned trace events and lifecycle recording for agent tests."""

from __future__ import annotations

import dataclasses
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, Field

from nanoharness.core.base import BaseHookManager, HookStage


TRACE_SCHEMA_VERSION = 1
REDACTED = "[REDACTED]"

_DEFAULT_SENSITIVE_FIELDS = frozenset(
    {
        "api_key",
        "authorization",
        "cookie",
        "password",
        "secret",
        "access_token",
        "refresh_token",
    }
)


class TraceEventType(str, Enum):
    """Stable event names shared by recorders, replayers, and adapters."""

    TASK_STARTED = "task_started"
    MODEL_RESPONSE = "model_response"
    STEP_COMPLETED = "step_completed"
    TASK_COMPLETED = "task_completed"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    STATE_SAVED = "state_saved"
    HOOK_FAILED = "hook_failed"
    CUSTOM = "custom"


class TraceEvent(BaseModel):
    """One immutable-by-convention fact in an agent execution trace."""

    schema_version: int = Field(default=TRACE_SCHEMA_VERSION, ge=1)
    event_id: str
    trace_id: str
    sequence: int = Field(ge=0)
    event_type: TraceEventType
    timestamp: float = Field(ge=0)
    payload: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AgentTrace(BaseModel):
    """A versioned, serializable sequence of normalized trace events."""

    schema_version: int = Field(default=TRACE_SCHEMA_VERSION, ge=1)
    trace_id: str
    started_at: float = Field(ge=0)
    events: List[TraceEvent] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


def _normalize(value: Any) -> Any:
    """Convert runtime objects into JSON-compatible NanoHarness values."""

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _normalize(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def redact_sensitive_fields(
    value: Any,
    *,
    sensitive_fields: Optional[Set[str]] = None,
) -> Any:
    """Return a copy with common secret-bearing mapping fields redacted.

    Field-name redaction cannot discover secrets embedded in arbitrary text.
    Applications handling such data should inject a stricter custom redactor.
    """

    fields = (
        {field.casefold() for field in sensitive_fields}
        if sensitive_fields is not None
        else _DEFAULT_SENSITIVE_FIELDS
    )
    if isinstance(value, Mapping):
        return {
            str(key): (
                REDACTED
                if str(key).casefold() in fields
                else redact_sensitive_fields(item, sensitive_fields=set(fields))
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_fields(item, sensitive_fields=set(fields)) for item in value]
    return value


class TraceRecorder:
    """Thread-safe recorder that can subscribe to NanoHarness lifecycle hooks.

    The recorder owns no execution policy. It normalizes and redacts hook data,
    assigns a stable sequence, and produces detached ``AgentTrace`` snapshots.
    More detailed tool and state events can be recorded explicitly through
    ``record`` until their runtime component contracts expose dedicated hooks.
    """

    _HOOK_EVENT_TYPES = {
        HookStage.ON_TASK_START: TraceEventType.TASK_STARTED,
        HookStage.ON_THOUGHT_READY: TraceEventType.MODEL_RESPONSE,
        HookStage.ON_STEP_END: TraceEventType.STEP_COMPLETED,
        HookStage.ON_TASK_END: TraceEventType.TASK_COMPLETED,
    }

    def __init__(
        self,
        *,
        trace_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], Any] = uuid.uuid4,
        redactor: Callable[[Any], Any] = redact_sensitive_fields,
    ):
        self._clock = clock
        self._id_factory = id_factory
        self._redactor = redactor
        self._lock = threading.RLock()
        self._attached_hook_managers: Set[int] = set()
        self._trace_id = trace_id or str(self._id_factory())
        self._started_at = self._clock()
        self._metadata = self._prepare(metadata or {})
        self._events: List[TraceEvent] = []

    @property
    def trace_id(self) -> str:
        return self._trace_id

    @property
    def events(self) -> List[TraceEvent]:
        """Return a detached copy so callers cannot mutate recorder state."""

        return self.snapshot().events

    def attach(self, hooks: BaseHookManager) -> None:
        """Subscribe to standard lifecycle stages, once per hook manager."""

        manager_id = id(hooks)
        with self._lock:
            if manager_id in self._attached_hook_managers:
                return
            self._attached_hook_managers.add(manager_id)

        for stage, event_type in self._HOOK_EVENT_TYPES.items():
            hooks.register(
                stage,
                lambda data, captured_type=event_type: self.record(
                    captured_type,
                    data,
                ),
            )

    def record(
        self,
        event_type: TraceEventType,
        data: Any = None,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TraceEvent:
        """Normalize and append one event, returning a detached event copy."""

        payload = self._prepare(data)
        if not isinstance(payload, dict):
            payload = {"data": payload}
        event_metadata = self._prepare(metadata or {})

        with self._lock:
            event = TraceEvent(
                event_id=str(self._id_factory()),
                trace_id=self._trace_id,
                sequence=len(self._events),
                event_type=event_type,
                timestamp=self._clock(),
                payload=payload,
                metadata=event_metadata,
            )
            self._events.append(event)
            return TraceEvent.model_validate(event.model_dump())

    def snapshot(self) -> AgentTrace:
        """Create a detached, serialization-ready trace snapshot."""

        with self._lock:
            return AgentTrace.model_validate(
                {
                    "trace_id": self._trace_id,
                    "started_at": self._started_at,
                    "events": [event.model_dump() for event in self._events],
                    "metadata": self._metadata,
                }
            )

    def reset(
        self,
        *,
        trace_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Start a new trace while retaining hook subscriptions."""

        with self._lock:
            self._trace_id = trace_id or str(self._id_factory())
            self._started_at = self._clock()
            self._metadata = self._prepare(metadata or {})
            self._events = []

    def _prepare(self, value: Any) -> Any:
        return self._redactor(_normalize(value))
