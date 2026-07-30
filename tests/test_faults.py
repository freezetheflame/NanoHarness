from typing import Any, Dict, List, Optional

import pytest
from pydantic import ValidationError

from nanoharness.components.context.simple_context import SimpleContextManager
from nanoharness.components.evaluator.trace_evaluator import TraceEvaluator
from nanoharness.components.hooks.simple_hooks import SimpleHookManager
from nanoharness.components.state.json_store import JsonStateStore
from nanoharness.components.tools.dict_registry import DictToolRegistry
from nanoharness.core.base import BaseStateStore, HookStage
from nanoharness.core.engine import NanoEngine
from nanoharness.core.schema import AgentMessage, LLMResponse, ToolCall
from nanoharness.testing import (
    FAULT_SCHEMA_VERSION,
    FaultAction,
    FaultCampaignConfigurationError,
    FaultCampaignRunner,
    FaultComponent,
    FaultInjectingContextManager,
    FaultInjectingHookManager,
    FaultInjectingLLM,
    FaultInjectingPermissionManager,
    FaultInjectingStateStore,
    FaultInjectingToolRegistry,
    FaultPlan,
    FaultRule,
    FaultSession,
    InjectedFaultError,
    MutationStatus,
    OracleKind,
    OracleSpec,
    RecordingLLM,
    RecordingContextManager,
    RecordingHookManager,
    RecordingPermissionManager,
    RecordingStateStore,
    RecordingToolRegistry,
    Scenario,
    TraceRecorder,
    TraceEventType,
)


class StaticLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.index = 0

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        response = self.responses[self.index]
        self.index += 1
        return response


def _plan(*rules):
    return FaultPlan(plan_id="fault-plan", rules=list(rules))


def _rule(rule_id, component, action, **kwargs):
    return FaultRule(
        rule_id=rule_id,
        component=component,
        action=action,
        **kwargs,
    )


def test_fault_plan_round_trips_and_rejects_duplicate_rule_ids():
    rule = _rule(
        "drop",
        FaultComponent.MODEL,
        FaultAction.MODEL_RESPONSE_DROP,
    )
    plan = _plan(rule)

    assert FaultPlan.model_validate_json(plan.model_dump_json()) == plan
    assert plan.schema_version == FAULT_SCHEMA_VERSION
    with pytest.raises(ValidationError, match="Duplicate fault rule IDs"):
        _plan(rule, rule)


def test_legacy_v1_fault_plan_is_upgraded_in_detached_session():
    plan = _plan(
        _rule(
            "legacy-drop",
            FaultComponent.MODEL,
            FaultAction.MODEL_RESPONSE_DROP,
        )
    )
    plan.schema_version = 1

    session = FaultSession(plan)

    assert session.plan.schema_version == FAULT_SCHEMA_VERSION
    assert plan.schema_version == 1


def test_fault_plan_normalizes_non_json_replacement_values():
    replacement = object()
    plan = _plan(
        _rule(
            "normalize",
            FaultComponent.TOOL,
            FaultAction.TOOL_RESULT_STALE,
            replacement=replacement,
        )
    )

    restored = FaultPlan.model_validate_json(plan.model_dump_json())

    assert restored.rules[0].replacement == repr(replacement)


def test_fault_report_redacts_sensitive_replacement_evidence():
    session = FaultSession(
        _plan(
            _rule(
                "secret",
                FaultComponent.TOOL,
                FaultAction.TOOL_RESULT_STALE,
                replacement={"password": "do-not-store"},
            )
        )
    )

    report = session.report()

    assert report.configured_rules[0].replacement["password"] == "[REDACTED]"


@pytest.mark.parametrize(
    "kwargs, message",
    [
        (
            {
                "component": FaultComponent.TOOL,
                "action": FaultAction.MODEL_RESPONSE_DROP,
            },
            "requires the model component",
        ),
        (
            {
                "component": FaultComponent.MODEL,
                "action": FaultAction.TOOL_RESULT_STALE,
            },
            "requires the tool component",
        ),
        (
            {
                "component": FaultComponent.MODEL,
                "action": FaultAction.TOOL_NAME_SWAP,
            },
            "requires replacement_tool_name",
        ),
    ],
)
def test_fault_rule_validates_action_contract(kwargs, message):
    with pytest.raises(ValidationError, match=message):
        FaultRule(rule_id="invalid", **kwargs)


def test_model_response_drop_uses_zero_based_occurrence():
    session = FaultSession(
        _plan(
            _rule(
                "drop-second",
                FaultComponent.MODEL,
                FaultAction.MODEL_RESPONSE_DROP,
                occurrence=1,
            )
        )
    )
    llm = FaultInjectingLLM(
        StaticLLM([LLMResponse(content="first"), LLMResponse(content="second")]),
        session,
    )

    assert llm.chat([]).content == "first"
    assert llm.chat([]) == LLMResponse(content="", tool_calls=None)
    assert session.report().effective_rule_ids == ["drop-second"]


def test_tool_name_swap_changes_a_matching_generated_call_without_aliasing():
    original = LLMResponse(
        content="call",
        tool_calls=[ToolCall(name="read", arguments={"path": "a"})],
    )
    session = FaultSession(
        _plan(
            _rule(
                "swap",
                FaultComponent.MODEL,
                FaultAction.TOOL_NAME_SWAP,
                tool_name="read",
                replacement_tool_name="write",
            )
        )
    )

    mutated = FaultInjectingLLM(StaticLLM([original]), session).chat([])

    assert mutated.tool_calls[0].name == "write"
    assert original.tool_calls[0].name == "read"
    assert session.report().applications[0].details["original_name"] == "read"


def test_injected_model_error_does_not_call_delegate():
    delegate = StaticLLM([LLMResponse(content="must not be consumed")])
    session = FaultSession(
        _plan(
            _rule(
                "model-down",
                FaultComponent.MODEL,
                FaultAction.RAISE_ERROR,
                message="model unavailable",
            )
        )
    )

    with pytest.raises(InjectedFaultError, match="model unavailable") as captured:
        FaultInjectingLLM(delegate, session).chat([])

    assert captured.value.rule_id == "model-down"
    assert delegate.index == 0


def _registry_with_counter():
    registry = DictToolRegistry()
    calls = []

    @registry.tool
    def echo(text: str):
        """Record and echo text."""
        calls.append(text)
        return f"echo:{text}:{len(calls)}"

    return registry, calls


def test_tool_argument_drop_mutates_delegate_input_but_not_caller_input():
    registry, _ = _registry_with_counter()
    session = FaultSession(
        _plan(
            _rule(
                "drop-text",
                FaultComponent.TOOL,
                FaultAction.TOOL_ARGUMENT_DROP,
                tool_name="echo",
                argument="text",
            )
        )
    )
    arguments = {"text": "hello"}

    with pytest.raises(TypeError):
        FaultInjectingToolRegistry(registry, session).call("echo", arguments)

    assert arguments == {"text": "hello"}
    assert session.report().effective_rule_ids == ["drop-text"]


def test_stale_result_and_duplicate_call_are_executable_and_reported():
    registry, calls = _registry_with_counter()
    session = FaultSession(
        _plan(
            _rule(
                "duplicate",
                FaultComponent.TOOL,
                FaultAction.TOOL_CALL_DUPLICATE,
                tool_name="echo",
            ),
            _rule(
                "stale",
                FaultComponent.TOOL,
                FaultAction.TOOL_RESULT_STALE,
                tool_name="echo",
                replacement="old",
            ),
        )
    )

    result = FaultInjectingToolRegistry(registry, session).call(
        "echo", {"text": "hello"}
    )

    assert result == "old"
    assert calls == ["hello", "hello"]
    assert session.report().effective_rule_ids == ["duplicate", "stale"]


def test_duplicate_fault_is_reported_when_the_second_execution_fails():
    registry, _ = _registry_with_counter()
    original_call = registry.call
    attempts = 0

    def fail_second(name, args):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise RuntimeError("second execution failed")
        return original_call(name, args)

    registry.call = fail_second
    session = FaultSession(
        _plan(
            _rule(
                "duplicate",
                FaultComponent.TOOL,
                FaultAction.TOOL_CALL_DUPLICATE,
                tool_name="echo",
            )
        )
    )

    with pytest.raises(RuntimeError, match="second execution failed"):
        FaultInjectingToolRegistry(registry, session).call(
            "echo", {"text": "hello"}
        )

    assert session.report().effective_rule_ids == ["duplicate"]


def test_recorder_aware_tool_fault_distinguishes_attempts_from_observation():
    registry, calls = _registry_with_counter()
    recorder = TraceRecorder()
    session = FaultSession(
        _plan(
            _rule(
                "duplicate",
                FaultComponent.TOOL,
                FaultAction.TOOL_CALL_DUPLICATE,
                tool_name="echo",
            ),
            _rule(
                "stale",
                FaultComponent.TOOL,
                FaultAction.TOOL_RESULT_STALE,
                tool_name="echo",
                replacement="old",
            ),
        )
    )
    tools = FaultInjectingToolRegistry(
        registry,
        session,
        recorder=recorder,
    )

    result = tools.call("echo", {"text": "hello"})

    assert result == "old"
    assert calls == ["hello", "hello"]
    events = recorder.snapshot().events
    assert [event.event_type for event in events] == [
        TraceEventType.TOOL_STARTED,
        TraceEventType.TOOL_COMPLETED,
        TraceEventType.TOOL_STARTED,
        TraceEventType.TOOL_COMPLETED,
        TraceEventType.TOOL_EXCHANGE,
    ]
    assert events[-1].payload["attempt_count"] == 2
    assert events[-1].payload["result"] == "old"


def test_recorder_aware_argument_drop_records_mutated_error_arguments():
    registry, _ = _registry_with_counter()
    recorder = TraceRecorder()
    session = FaultSession(
        _plan(
            _rule(
                "drop",
                FaultComponent.TOOL,
                FaultAction.TOOL_ARGUMENT_DROP,
                tool_name="echo",
                argument="text",
            )
        )
    )

    with pytest.raises(TypeError):
        FaultInjectingToolRegistry(
            registry,
            session,
            recorder=recorder,
        ).call("echo", {"text": "hello"})

    events = recorder.snapshot().events
    assert events[-1].event_type is TraceEventType.TOOL_ERROR
    assert events[-1].payload["arguments"] == {}
    assert events[-1].payload["attempt_count"] == 1


def test_ineffective_replacement_is_preserved_as_evidence():
    registry = DictToolRegistry()

    @registry.tool
    def fixed():
        """Return a fixed value."""
        return "same"

    session = FaultSession(
        _plan(
            _rule(
                "same-result",
                FaultComponent.TOOL,
                FaultAction.TOOL_RESULT_STALE,
                tool_name="fixed",
                replacement="same",
            )
        )
    )

    result = FaultInjectingToolRegistry(registry, session).call("fixed", {})

    assert result == "same"
    assert session.report().applied_rule_ids == ["same-result"]
    assert session.report().effective_rule_ids == []


def test_shared_session_produces_one_ordered_report_and_can_reset():
    session = FaultSession(
        _plan(
            _rule(
                "drop",
                FaultComponent.MODEL,
                FaultAction.MODEL_RESPONSE_DROP,
            ),
            _rule(
                "tool-down",
                FaultComponent.TOOL,
                FaultAction.RAISE_ERROR,
                tool_name="missing",
            ),
        )
    )
    llm = FaultInjectingLLM(StaticLLM([LLMResponse(content="answer")]), session)
    tools = FaultInjectingToolRegistry(DictToolRegistry(), session)

    llm.chat([])
    with pytest.raises(InjectedFaultError):
        tools.call("missing", {})
    restored = type(session.report()).model_validate_json(
        session.report().model_dump_json()
    )

    assert restored.applied_rule_ids == ["drop", "tool-down"]
    assert [item.sequence for item in restored.applications] == [0, 1]
    assert restored.untriggered_rule_ids == []
    session.reset()
    assert session.report().applications == []


class MemoryStateStore(BaseStateStore):
    def __init__(self):
        self.value = {}
        self.save_count = 0

    def save_state(self, state):
        self.value = dict(state)
        self.save_count += 1

    def load_state(self):
        return dict(self.value)

    def reset(self):
        self.value = {}


class Permission:
    def __init__(self, denial):
        self.denial = denial

    def enforce(self, tool_name, args):
        return self.denial


def test_context_message_drop_is_role_scoped_and_executable():
    context = SimpleContextManager()
    session = FaultSession(
        _plan(
            _rule(
                "drop-user",
                FaultComponent.CONTEXT,
                FaultAction.CONTEXT_MESSAGE_DROP,
                message_role="user",
            )
        )
    )
    injected = FaultInjectingContextManager(context, session)

    injected.add_message(AgentMessage(role="system", content="keep"))
    injected.add_message(AgentMessage(role="user", content="drop"))

    assert [message["content"] for message in injected.get_full_context()] == ["keep"]
    application = session.report().applications[0]
    assert application.message_role == "user"


def test_checkpoint_corruption_does_not_alias_caller_state():
    state = MemoryStateStore()
    session = FaultSession(
        _plan(
            _rule(
                "corrupt-step",
                FaultComponent.STATE,
                FaultAction.CHECKPOINT_CORRUPT,
                state_key="current_step",
                replacement=99,
            )
        )
    )
    original = {"current_step": 1, "status": "ok"}

    FaultInjectingStateStore(state, session).save_state(original)

    assert original["current_step"] == 1
    assert state.value["current_step"] == 99
    assert session.report().applications[0].state_key == "current_step"


def test_state_save_drop_skips_delegate():
    state = MemoryStateStore()
    session = FaultSession(
        _plan(
            _rule(
                "drop-save",
                FaultComponent.STATE,
                FaultAction.STATE_SAVE_DROP,
            )
        )
    )

    FaultInjectingStateStore(state, session).save_state({"step": 1})

    assert state.save_count == 0
    assert session.report().effective_rule_ids == ["drop-save"]


def test_hook_skip_prevents_registered_callback():
    called = []
    hooks = SimpleHookManager()
    hooks.register(HookStage.ON_TASK_END, lambda data: called.append(data))
    session = FaultSession(
        _plan(
            _rule(
                "skip-end",
                FaultComponent.HOOK,
                FaultAction.HOOK_SKIP,
                hook_stage=HookStage.ON_TASK_END.value,
            )
        )
    )

    FaultInjectingHookManager(hooks, session).trigger(HookStage.ON_TASK_END, "done")

    assert called == []
    assert session.report().applications[0].hook_stage == "on_task_end"


@pytest.mark.parametrize(
    "denial, effective",
    [("denied", True), (None, False)],
)
def test_permission_bypass_reports_whether_a_denial_changed(denial, effective):
    session = FaultSession(
        _plan(
            _rule(
                "bypass",
                FaultComponent.PERMISSION,
                FaultAction.PERMISSION_BYPASS,
                tool_name="write",
            )
        )
    )

    result = FaultInjectingPermissionManager(
        Permission(denial), session
    ).enforce("write", {})

    assert result is None
    assert session.report().applications[0].effective is effective


@pytest.mark.parametrize(
    "component, action, kwargs, message",
    [
        (
            FaultComponent.TOOL,
            FaultAction.CONTEXT_MESSAGE_DROP,
            {},
            "requires the context component",
        ),
        (
            FaultComponent.STATE,
            FaultAction.CHECKPOINT_CORRUPT,
            {},
            "requires state_key",
        ),
        (
            FaultComponent.HOOK,
            FaultAction.HOOK_SKIP,
            {},
            "requires hook_stage",
        ),
        (
            FaultComponent.CONTEXT,
            FaultAction.CONTEXT_MESSAGE_DROP,
            {"tool_name": "echo"},
            "unrelated selectors",
        ),
    ],
)
def test_runtime_fault_rules_validate_boundary_contracts(
    component,
    action,
    kwargs,
    message,
):
    with pytest.raises(ValidationError, match=message):
        FaultRule(
            rule_id="invalid-runtime",
            component=component,
            action=action,
            **kwargs,
        )


class TestFaultCampaign:
    @staticmethod
    def engine_factory(scenario, recorder, session):
        registry, _ = _registry_with_counter()
        llm = StaticLLM([
            LLMResponse(
                content="echo",
                tool_calls=[ToolCall(name="echo", arguments={"text": "hello"})],
            ),
            LLMResponse(content="done"),
        ])
        if session is not None:
            llm = FaultInjectingLLM(llm, session)
            registry = FaultInjectingToolRegistry(registry, session)
        return NanoEngine(
            llm_client=RecordingLLM(llm, recorder),
            tools=RecordingToolRegistry(registry, recorder),
            context=SimpleContextManager(),
            state=JsonStateStore(f"/tmp/{scenario.scenario_id}-fault.json"),
            hooks=SimpleHookManager(),
            evaluator=TraceEvaluator(),
        )

    @staticmethod
    def scenario(expected="hello"):
        return Scenario(
            scenario_id="fault-campaign",
            query="echo hello",
            oracles=[
                OracleSpec(
                    kind=OracleKind.TOOL_RESULTS,
                    parameters={"expected_last": {"echo": expected}},
                )
            ],
        )

    def test_campaign_classifies_killed_survived_equivalent_and_not_applicable(self):
        plans = [
            FaultPlan(
                plan_id="killed",
                rules=[
                    _rule(
                        "stale",
                        FaultComponent.TOOL,
                        FaultAction.TOOL_RESULT_STALE,
                        tool_name="echo",
                        replacement="old",
                    )
                ],
            ),
            FaultPlan(
                plan_id="survived",
                rules=[
                    _rule(
                        "duplicate",
                        FaultComponent.TOOL,
                        FaultAction.TOOL_CALL_DUPLICATE,
                        tool_name="echo",
                    )
                ],
            ),
            FaultPlan(
                plan_id="survived-terminal-drop",
                rules=[
                    _rule(
                        "drop-terminal-response",
                        FaultComponent.MODEL,
                        FaultAction.MODEL_RESPONSE_DROP,
                        occurrence=1,
                    )
                ],
            ),
            FaultPlan(
                plan_id="equivalent",
                rules=[
                    _rule(
                        "same",
                        FaultComponent.TOOL,
                        FaultAction.TOOL_RESULT_STALE,
                        tool_name="echo",
                        replacement="echo:hello:1",
                    )
                ],
            ),
            FaultPlan(
                plan_id="not-applicable",
                rules=[
                    _rule(
                        "missing",
                        FaultComponent.TOOL,
                        FaultAction.TOOL_CALL_DUPLICATE,
                        tool_name="missing",
                    )
                ],
            ),
        ]

        campaign = FaultCampaignRunner(self.engine_factory).run(
            self.scenario(expected="echo:hello:1"),
            plans,
        )

        assert [outcome.status for outcome in campaign.outcomes] == [
            MutationStatus.KILLED,
            MutationStatus.KILLED,
            MutationStatus.SURVIVED,
            MutationStatus.EQUIVALENT,
            MutationStatus.NOT_APPLICABLE,
        ]
        assert campaign.killed == 2
        assert campaign.survived == 1
        assert campaign.equivalent == 1
        assert campaign.not_applicable == 1
        assert campaign.mutation_score == pytest.approx(2 / 3)
        killed_trace = campaign.outcomes[0].scenario_report.trace
        tool_events = [
            event for event in killed_trace.events
            if event.event_type.value == "tool_exchange"
        ]
        assert tool_events[0].payload["result"] == "old"
        assert killed_trace.metadata["execution_mode"] == "fault_injected"
        assert killed_trace.metadata["fault_plan_id"] == "killed"
        restored = type(campaign).model_validate_json(campaign.model_dump_json())
        assert restored == campaign

    def test_campaign_reports_baseline_failure_without_executing_plans(self):
        campaign = FaultCampaignRunner(self.engine_factory).run(
            self.scenario(expected="wrong"),
            [FaultPlan(plan_id="unused", rules=[])],
        )

        assert campaign.outcomes[0].status is MutationStatus.BASELINE_FAILED
        assert campaign.outcomes[0].scenario_report is None
        assert campaign.mutation_score is None

    def test_duplicate_plan_ids_are_rejected_before_engine_creation(self):
        called = False

        def factory(scenario, recorder, session):
            nonlocal called
            called = True
            return self.engine_factory(scenario, recorder, session)

        plan = FaultPlan(plan_id="duplicate", rules=[])
        with pytest.raises(FaultCampaignConfigurationError, match="Duplicate"):
            FaultCampaignRunner(factory).run(self.scenario(), [plan, plan])

        assert called is False

    def test_fault_setup_error_is_not_misclassified_as_not_applicable(self):
        def factory(scenario, recorder, session):
            if session is not None:
                raise RuntimeError("cannot create injected fixture")
            return self.engine_factory(scenario, recorder, session)

        campaign = FaultCampaignRunner(factory).run(
            self.scenario(expected="echo:hello:1"),
            [FaultPlan(plan_id="broken", rules=[])],
        )

        assert campaign.outcomes[0].status is MutationStatus.ERROR
        assert campaign.errors == 1
        assert campaign.not_applicable == 0
        assert campaign.mutation_score is None

    def test_permission_bypass_is_killed_by_execution_order_oracle(self):
        def factory(scenario, recorder, session):
            registry, _ = _registry_with_counter()
            permissions = RecordingPermissionManager(
                Permission("denied by policy"),
                recorder,
            )
            if session is not None:
                permissions = FaultInjectingPermissionManager(permissions, session)
            return NanoEngine(
                llm_client=RecordingLLM(
                    StaticLLM([
                        LLMResponse(
                            content="echo",
                            tool_calls=[
                                ToolCall(name="echo", arguments={"text": "hello"})
                            ],
                        ),
                        LLMResponse(content="done"),
                    ]),
                    recorder,
                ),
                tools=RecordingToolRegistry(registry, recorder),
                context=SimpleContextManager(),
                state=JsonStateStore("/tmp/fault-permission.json"),
                hooks=SimpleHookManager(),
                evaluator=TraceEvaluator(),
                permissions=permissions,
            )

        scenario = Scenario(
            scenario_id="permission-bypass",
            query="echo hello",
            oracles=[OracleSpec(kind=OracleKind.PERMISSION_ENFORCEMENT)],
        )
        plan = FaultPlan(
            plan_id="bypass",
            rules=[
                _rule(
                    "bypass-echo",
                    FaultComponent.PERMISSION,
                    FaultAction.PERMISSION_BYPASS,
                    tool_name="echo",
                )
            ],
        )

        campaign = FaultCampaignRunner(factory).run(scenario, [plan])

        assert campaign.baseline.passed is True
        assert campaign.outcomes[0].status is MutationStatus.KILLED
        assert campaign.outcomes[0].fault_report.effective_rule_ids == [
            "bypass-echo"
        ]

    def test_context_and_hook_control_flow_faults_are_executed(self):
        def factory(scenario, recorder, session):
            context = RecordingContextManager(SimpleContextManager(), recorder)
            hooks = RecordingHookManager(SimpleHookManager(), recorder)
            if session is not None:
                context = FaultInjectingContextManager(context, session)
                hooks = FaultInjectingHookManager(hooks, session)
            return NanoEngine(
                llm_client=RecordingLLM(
                    StaticLLM([LLMResponse(content="done")]),
                    recorder,
                ),
                tools=RecordingToolRegistry(DictToolRegistry(), recorder),
                context=context,
                state=JsonStateStore("/tmp/fault-context-hook.json"),
                hooks=hooks,
                evaluator=TraceEvaluator(),
            )

        context_scenario = Scenario(
            scenario_id="context-drop-live",
            query="retain this goal",
            oracles=[OracleSpec(kind=OracleKind.MODEL_MESSAGES)],
        )
        context_plan = FaultPlan(
            plan_id="drop-context",
            rules=[
                _rule(
                    "drop-user",
                    FaultComponent.CONTEXT,
                    FaultAction.CONTEXT_MESSAGE_DROP,
                    message_role="user",
                )
            ],
        )
        hook_scenario = Scenario(
            scenario_id="hook-skip-live",
            query="finish",
            oracles=[OracleSpec(kind=OracleKind.LIFECYCLE)],
        )
        hook_plan = FaultPlan(
            plan_id="skip-hook",
            rules=[
                _rule(
                    "skip-task-end",
                    FaultComponent.HOOK,
                    FaultAction.HOOK_SKIP,
                    hook_stage=HookStage.ON_TASK_END.value,
                )
            ],
        )

        context_campaign = FaultCampaignRunner(factory).run(
            context_scenario,
            [context_plan],
        )
        hook_campaign = FaultCampaignRunner(factory).run(
            hook_scenario,
            [hook_plan],
        )

        assert context_campaign.outcomes[0].status is MutationStatus.KILLED
        assert hook_campaign.outcomes[0].status is MutationStatus.KILLED

    def test_checkpoint_corruption_is_killed_by_state_oracle(self):
        def factory(scenario, recorder, session):
            state = RecordingStateStore(MemoryStateStore(), recorder)
            if session is not None:
                state = FaultInjectingStateStore(state, session)
            return NanoEngine(
                llm_client=RecordingLLM(
                    StaticLLM([LLMResponse(content="done")]),
                    recorder,
                ),
                tools=RecordingToolRegistry(DictToolRegistry(), recorder),
                context=SimpleContextManager(),
                state=state,
                hooks=SimpleHookManager(),
                evaluator=TraceEvaluator(),
            )

        scenario = Scenario(
            scenario_id="checkpoint-corruption",
            query="finish",
            oracles=[
                OracleSpec(
                    kind=OracleKind.STATE_VALUES,
                    parameters={
                        "required_keys": ["current_step", "status"],
                        "expected_last": {
                            "current_step": 0,
                            "status": "terminated",
                        },
                    },
                )
            ],
        )
        plan = FaultPlan(
            plan_id="corrupt-checkpoint",
            rules=[
                _rule(
                    "corrupt-step",
                    FaultComponent.STATE,
                    FaultAction.CHECKPOINT_CORRUPT,
                    state_key="current_step",
                    replacement=99,
                )
            ],
        )

        campaign = FaultCampaignRunner(factory).run(scenario, [plan])

        assert campaign.baseline.passed is True
        assert campaign.outcomes[0].status is MutationStatus.KILLED
