"""Recording decorators for Context, state, hook, and permission boundaries."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Dict, Optional, Protocol

from nanoharness.core.base import (
    BaseContextManager,
    BaseHookManager,
    BaseStateStore,
)
from nanoharness.core.schema import AgentMessage
from nanoharness.testing.trace import TraceEventType, TraceRecorder


class PermissionProtocol(Protocol):
    """Minimal permission boundary used by ``NanoEngine``."""

    def enforce(self, tool_name: str, args: Dict) -> Optional[str]:
        ...


class RecordingContextManager(BaseContextManager):
    """Context decorator recording messages, snapshots, and failures."""

    def __init__(
        self,
        delegate: BaseContextManager,
        recorder: TraceRecorder,
        *,
        clock: Callable[[], float] = time.perf_counter,
    ):
        self._delegate = delegate
        self._recorder = recorder
        self._clock = clock

    def add_message(self, msg: AgentMessage):
        started_at = self._clock()
        try:
            result = self._delegate.add_message(msg)
        except Exception as exc:
            self._record_error("add_message", exc, started_at)
            raise
        self._recorder.record(
            TraceEventType.CONTEXT_MESSAGE_ADDED,
            {
                "message": msg,
                "duration_ms": (self._clock() - started_at) * 1000,
            },
        )
        return result

    def get_full_context(self):
        started_at = self._clock()
        try:
            messages = self._delegate.get_full_context()
        except Exception as exc:
            self._record_error("get_full_context", exc, started_at)
            raise
        self._recorder.record(
            TraceEventType.CONTEXT_SNAPSHOT,
            {
                "messages": messages,
                "duration_ms": (self._clock() - started_at) * 1000,
            },
        )
        return messages

    def reset(self):
        return self._delegate.reset()

    def _record_error(self, operation: str, exc: Exception, started_at: float) -> None:
        self._recorder.record(
            TraceEventType.CONTEXT_ERROR,
            {
                "operation": operation,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "duration_ms": (self._clock() - started_at) * 1000,
            },
        )


class RecordingStateStore(BaseStateStore):
    """State decorator recording save/load values and failures."""

    def __init__(
        self,
        delegate: BaseStateStore,
        recorder: TraceRecorder,
        *,
        clock: Callable[[], float] = time.perf_counter,
    ):
        self._delegate = delegate
        self._recorder = recorder
        self._clock = clock

    def save_state(self, state: Dict):
        started_at = self._clock()
        try:
            result = self._delegate.save_state(state)
        except Exception as exc:
            self._record_error("save_state", exc, started_at)
            raise
        self._recorder.record(
            TraceEventType.STATE_SAVED,
            {
                "state": state,
                "duration_ms": (self._clock() - started_at) * 1000,
            },
        )
        return result

    def load_state(self) -> Dict:
        started_at = self._clock()
        try:
            state = self._delegate.load_state()
        except Exception as exc:
            self._record_error("load_state", exc, started_at)
            raise
        self._recorder.record(
            TraceEventType.STATE_LOADED,
            {
                "state": state,
                "duration_ms": (self._clock() - started_at) * 1000,
            },
        )
        return state

    def reset(self):
        return self._delegate.reset()

    def _record_error(self, operation: str, exc: Exception, started_at: float) -> None:
        self._recorder.record(
            TraceEventType.STATE_ERROR,
            {
                "operation": operation,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "duration_ms": (self._clock() - started_at) * 1000,
            },
        )


class RecordingHookManager(BaseHookManager):
    """Hook decorator recording stage invocation, completion, and failure."""

    def __init__(
        self,
        delegate: BaseHookManager,
        recorder: TraceRecorder,
        *,
        clock: Callable[[], float] = time.perf_counter,
    ):
        self._delegate = delegate
        self._recorder = recorder
        self._clock = clock

    def register(self, stage: str, hook):
        return self._delegate.register(stage, hook)

    def trigger(self, stage: str, data: Any):
        started_at = self._clock()
        self._recorder.record(
            TraceEventType.HOOK_STARTED,
            {"stage": _stage_value(stage)},
        )
        try:
            result = self._delegate.trigger(stage, data)
        except Exception as exc:
            self._recorder.record(
                TraceEventType.HOOK_FAILED,
                {
                    "stage": _stage_value(stage),
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "duration_ms": (self._clock() - started_at) * 1000,
                },
            )
            raise
        self._recorder.record(
            TraceEventType.HOOK_COMPLETED,
            {
                "stage": _stage_value(stage),
                "duration_ms": (self._clock() - started_at) * 1000,
            },
        )
        return result

    def reset(self):
        return self._delegate.reset()


class RecordingPermissionManager:
    """Duck-typed permission decorator recording allow/deny and failures."""

    def __init__(
        self,
        delegate: PermissionProtocol,
        recorder: TraceRecorder,
        *,
        clock: Callable[[], float] = time.perf_counter,
    ):
        self._delegate = delegate
        self._recorder = recorder
        self._clock = clock

    def enforce(self, tool_name: str, args: Dict) -> Optional[str]:
        started_at = self._clock()
        try:
            denial = self._delegate.enforce(tool_name, args)
        except Exception as exc:
            self._recorder.record(
                TraceEventType.PERMISSION_ERROR,
                {
                    "tool_name": tool_name,
                    "arguments": args,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "duration_ms": (self._clock() - started_at) * 1000,
                },
            )
            raise
        self._recorder.record(
            TraceEventType.PERMISSION_DECISION,
            {
                "tool_name": tool_name,
                "arguments": args,
                "allowed": denial is None,
                "denial": denial,
                "duration_ms": (self._clock() - started_at) * 1000,
            },
        )
        return denial

    def reset(self):
        reset = getattr(self._delegate, "reset", None)
        if reset is not None:
            return reset()
        return None


def _stage_value(stage: Any) -> str:
    return str(getattr(stage, "value", stage))
