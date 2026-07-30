from typing import Any, Dict

import pytest

from nanoharness.components.context.simple_context import SimpleContextManager
from nanoharness.components.hooks.simple_hooks import SimpleHookManager
from nanoharness.core.base import BaseStateStore, HookStage
from nanoharness.core.schema import AgentMessage
from nanoharness.testing import (
    OracleKind,
    OracleSpec,
    RecordingContextManager,
    RecordingHookManager,
    RecordingPermissionManager,
    RecordingStateStore,
    Scenario,
    ScenarioReport,
    TraceEventType,
    TraceRecorder,
)
from nanoharness.testing.oracle import OracleEvaluator


class MemoryStateStore(BaseStateStore):
    def __init__(self):
        self.value = {}

    def save_state(self, state: Dict):
        self.value = dict(state)

    def load_state(self) -> Dict:
        return dict(self.value)

    def reset(self):
        self.value = {}


class Permission:
    def __init__(self, denial=None):
        self.denial = denial

    def enforce(self, tool_name, args):
        return self.denial


def _types(recorder):
    return [event.event_type for event in recorder.snapshot().events]


def test_context_recorder_captures_messages_snapshots_and_redacts():
    recorder = TraceRecorder()
    context = RecordingContextManager(SimpleContextManager(), recorder)

    context.add_message(
        AgentMessage(role="user", content="hello", tool_calls=None)
    )
    messages = context.get_full_context()

    assert messages[0]["content"] == "hello"
    assert _types(recorder) == [
        TraceEventType.CONTEXT_MESSAGE_ADDED,
        TraceEventType.CONTEXT_SNAPSHOT,
    ]


def test_state_recorder_captures_save_load_and_sensitive_field_redaction():
    recorder = TraceRecorder()
    delegate = MemoryStateStore()
    state = RecordingStateStore(delegate, recorder)

    state.save_state({"step": 2, "password": "secret"})
    loaded = state.load_state()

    assert loaded == {"step": 2, "password": "secret"}
    events = recorder.snapshot().events
    assert [event.event_type for event in events] == [
        TraceEventType.STATE_SAVED,
        TraceEventType.STATE_LOADED,
    ]
    assert events[0].payload["state"]["password"] == "[REDACTED]"
    assert delegate.value["password"] == "secret"


def test_hook_recorder_preserves_lifecycle_callback_order():
    recorder = TraceRecorder()
    hooks = RecordingHookManager(SimpleHookManager(), recorder)
    recorder.attach(hooks)

    hooks.trigger(HookStage.ON_TASK_START, "work")

    assert _types(recorder) == [
        TraceEventType.HOOK_STARTED,
        TraceEventType.TASK_STARTED,
        TraceEventType.HOOK_COMPLETED,
    ]


@pytest.mark.parametrize(
    "denial, allowed",
    [(None, True), ("Permission denied", False)],
)
def test_permission_recorder_captures_decision(denial, allowed):
    recorder = TraceRecorder()
    permissions = RecordingPermissionManager(Permission(denial), recorder)

    result = permissions.enforce("write", {"path": "a"})

    assert result == denial
    event = recorder.snapshot().events[0]
    assert event.event_type is TraceEventType.PERMISSION_DECISION
    assert event.payload["allowed"] is allowed


@pytest.mark.parametrize(
    "wrapper_factory, call, error_type, component",
    [
        (
            lambda recorder: RecordingContextManager(FailingContext(), recorder),
            lambda wrapper: wrapper.get_full_context(),
            TraceEventType.CONTEXT_ERROR,
            "context",
        ),
        (
            lambda recorder: RecordingStateStore(FailingState(), recorder),
            lambda wrapper: wrapper.load_state(),
            TraceEventType.STATE_ERROR,
            "state",
        ),
        (
            lambda recorder: RecordingHookManager(FailingHooks(), recorder),
            lambda wrapper: wrapper.trigger("stage", None),
            TraceEventType.HOOK_FAILED,
            "hook",
        ),
        (
            lambda recorder: RecordingPermissionManager(FailingPermission(), recorder),
            lambda wrapper: wrapper.enforce("write", {}),
            TraceEventType.PERMISSION_ERROR,
            "permission",
        ),
    ],
)
def test_runtime_errors_are_recorded_and_detected_by_component_oracle(
    wrapper_factory,
    call,
    error_type,
    component,
):
    recorder = TraceRecorder()
    wrapper = wrapper_factory(recorder)

    with pytest.raises(RuntimeError, match="boundary failed"):
        call(wrapper)

    trace = recorder.snapshot()
    assert trace.events[-1].event_type is error_type
    scenario = Scenario(
        scenario_id=f"{component}-error",
        query="fail",
        oracles=[
            OracleSpec(
                kind=OracleKind.COMPONENT_ERRORS,
                parameters={"allowed_components": []},
            )
        ],
    )
    verdicts = OracleEvaluator().evaluate(
        scenario,
        result=None,
        trace=trace,
        execution_error=None,
    )
    report = ScenarioReport(
        scenario_id=scenario.scenario_id,
        passed=False,
        trace=trace,
        verdicts=verdicts,
    )
    assert report.verdicts[0].passed is False
    assert report.verdicts[0].evidence["errors"][0]["component"] == component


class FailingContext(SimpleContextManager):
    def get_full_context(self):
        raise RuntimeError("boundary failed")


class FailingState(MemoryStateStore):
    def load_state(self):
        raise RuntimeError("boundary failed")


class FailingHooks(SimpleHookManager):
    def trigger(self, stage, data: Any):
        raise RuntimeError("boundary failed")


class FailingPermission(Permission):
    def enforce(self, tool_name, args):
        raise RuntimeError("boundary failed")
