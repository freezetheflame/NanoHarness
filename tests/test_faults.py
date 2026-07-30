from typing import Any, Dict, List, Optional

import pytest
from pydantic import ValidationError

from nanoharness.components.tools.dict_registry import DictToolRegistry
from nanoharness.components.context.simple_context import SimpleContextManager
from nanoharness.components.evaluator.trace_evaluator import TraceEvaluator
from nanoharness.components.hooks.simple_hooks import SimpleHookManager
from nanoharness.components.state.json_store import JsonStateStore
from nanoharness.core.engine import NanoEngine
from nanoharness.core.schema import LLMResponse, ToolCall
from nanoharness.testing import (
    FAULT_SCHEMA_VERSION,
    FaultAction,
    FaultCampaignConfigurationError,
    FaultCampaignRunner,
    FaultComponent,
    FaultInjectingLLM,
    FaultInjectingToolRegistry,
    FaultPlan,
    FaultRule,
    FaultSession,
    InjectedFaultError,
    MutationStatus,
    OracleKind,
    OracleSpec,
    RecordingLLM,
    RecordingToolRegistry,
    Scenario,
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
