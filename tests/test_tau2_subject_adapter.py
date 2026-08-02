from copy import deepcopy
from datetime import datetime, timezone
from enum import Enum
from types import SimpleNamespace
from typing import Any, Optional

from pydantic import BaseModel, Field

from nanoharness.testing import (
    TAU2_NATIVE_BINDING,
    BenchmarkSourceIdentity,
    OracleKind,
    SubjectIdentity,
    Tau2BenchmarkAdapter,
    Tau2SubjectAdapter,
    TraceEventType,
)


class FakeAction(BaseModel):
    action_id: str
    requestor: str = "assistant"
    name: str
    arguments: dict


class FakeCriteria(BaseModel):
    actions: list[FakeAction]
    env_assertions: Optional[list] = None
    communicate_info: Optional[list[str]] = None
    nl_assertions: Optional[list[str]] = None
    reward_basis: list[str] = Field(default_factory=lambda: ["DB"])


class FakeTask(BaseModel):
    id: str
    user_scenario: dict
    initial_state: None = None
    description: None = None
    evaluation_criteria: FakeCriteria
    issues: list = Field(default_factory=list)


class FakeCall(BaseModel):
    id: str
    name: str
    arguments: dict
    requestor: str = "assistant"


class FakeParticipantMessage(BaseModel):
    role: str
    content: Optional[str] = None
    tool_calls: Optional[list[FakeCall]] = None


class FakeToolMessage(BaseModel):
    role: str = "tool"
    id: str
    content: str = "ok"
    error: bool = False


class FakeDB(BaseModel):
    orders: dict[str, dict[str, Any]] = Field(
        default_factory=lambda: {"#1": {"status": "delivered"}}
    )
    audit_count: int = 0


class FakeToolkit:
    def __init__(self):
        self.db = FakeDB()

    def has_tool(self, name):
        return name in {
            "exchange_item",
            "exchange_item_alternative",
            "read_missing_order",
            "read_order",
        }

    def tool_mutates_state(self, name):
        return name not in {"read_missing_order", "read_order"}


class FakeEnvironment:
    def __init__(self, solo_mode=False):
        assert solo_mode is False
        self.tools = FakeToolkit()
        self.user_tools = None

    def set_state(
        self,
        initialization_data,
        initialization_actions,
        message_history,
        strict=True,
    ):
        assert initialization_data is None
        assert initialization_actions is None
        assert strict is True
        calls = []
        for message in message_history:
            if message.role in {"assistant", "user"}:
                calls.extend(message.tool_calls or [])
        for call in calls:
            self.make_tool_call(
                tool_name=call.name,
                requestor=call.requestor,
                **call.arguments,
            )

    def make_tool_call(self, tool_name, requestor="assistant", **kwargs):
        assert requestor == "assistant"
        if tool_name in {"exchange_item", "exchange_item_alternative"}:
            self.tools.db.orders[kwargs["order_id"]]["status"] = "exchanged"
            return "ok"
        if tool_name == "read_order":
            return self.tools.db.orders[kwargs["order_id"]]
        if tool_name == "read_missing_order":
            raise ValueError("Order not found")
        raise ValueError(tool_name)


class FakeDBCheck(BaseModel):
    db_reward: float


class FakeRewardInfo(BaseModel):
    reward: float
    db_check: FakeDBCheck


class FakeEnvironmentEvaluator:
    @classmethod
    def calculate_reward(
        cls,
        environment_constructor,
        task,
        full_trajectory,
        solo_mode,
        env_kwargs,
        strict_replay,
    ):
        predicted = environment_constructor(solo_mode=solo_mode, **env_kwargs)
        predicted.set_state(None, None, full_trajectory, strict=strict_replay)
        gold = environment_constructor(solo_mode=solo_mode, **env_kwargs)
        gold.set_state(None, None, [], strict=strict_replay)
        for action in task.evaluation_criteria.actions:
            gold.make_tool_call(
                tool_name=action.name,
                requestor=action.requestor,
                **action.arguments,
            )
        reward = float(predicted.tools.db == gold.tools.db)
        return FakeRewardInfo(reward=reward, db_check=FakeDBCheck(db_reward=reward))


class FakeTermination(Enum):
    AGENT_STOP = "agent_stop"


RAW_TASK = {
    "id": "7",
    "initial_state": None,
    "user_scenario": {
        "instructions": {
            "domain": "retail",
            "reason_for_call": "Exchange one item.",
        }
    },
    "description": None,
    "evaluation_criteria": {
        "actions": [
            {
                "action_id": "7_0",
                "requestor": "assistant",
                "name": "exchange_item",
                "arguments": {"order_id": "#1"},
            }
        ],
        "reward_basis": ["DB"],
        "nl_assertions": None,
    },
    "issues": [],
}


def _manifest(raw_task=RAW_TASK):
    source = BenchmarkSourceIdentity(
        benchmark_id="tau2@test",
        distribution="tau2",
        package_version="test",
        revision="0123456789abcdef",
        source_url="https://github.com/sierra-research/tau2-bench",
        license="MIT",
    )
    return Tau2BenchmarkAdapter(
        source,
        [raw_task],
        tasks_sha256="1" * 64,
        domain_db={"orders": {"#1": {"status": "delivered"}}},
        domain_db_sha256="2" * 64,
    ).convert_tasks(
        "retail",
        ["7"],
        frozen_at=datetime(2026, 7, 31, tzinfo=timezone.utc),
    )


def _identity():
    return SubjectIdentity(
        subject_id="fake-tau2-subject",
        runtime="tau2",
        version="test",
        revision="subject-revision",
    )


def _messages(tool_names):
    messages = []
    for index, name in enumerate(tool_names):
        call = FakeCall(
            id=f"call-{index}",
            name=name,
            arguments={"order_id": "#1"},
        )
        messages.extend(
            [
                FakeParticipantMessage(role="assistant", tool_calls=[call]),
                FakeToolMessage(id=call.id),
            ]
        )
    messages.append(FakeParticipantMessage(role="assistant", content="Done."))
    return messages


def _adapter(tool_names, raw_task=RAW_TASK):
    task = FakeTask.model_validate(raw_task)

    def factory(scenario, native_task, recorder):
        return SimpleNamespace(
            task_id=native_task.id,
            termination_reason=FakeTermination.AGENT_STOP,
            messages=_messages(tool_names),
        )

    return Tau2SubjectAdapter(
        _identity(),
        _manifest(raw_task),
        factory,
        lambda: [task],
        FakeEnvironment,
        FakeEnvironmentEvaluator,
    )


def test_tau2_native_bridge_binds_t0_state_and_side_effect_oracles():
    adapter = _adapter(["exchange_item"])
    scenario = adapter.scenario_for("7", seed=7)

    report = adapter.run(scenario)

    assert scenario.metadata["benchmark"]["oracle_binding"] == TAU2_NATIVE_BINDING
    assert [oracle.kind for oracle in scenario.oracles] == [
        OracleKind.GOAL_ACHIEVEMENT.value,
        OracleKind.STATE_DELTA.value,
        OracleKind.SIDE_EFFECTS.value,
    ]
    assert report.passed is True
    assert report.result.evaluation.achieved is True
    assert report.result.final_answer == "Done."
    assert all(verdict.passed for verdict in report.verdicts)
    assert any(
        event.payload.get("adapter_event") == "native_db_scored"
        and event.payload["t0_view"] == "T0-DB"
        for event in report.trace.events
    )


def test_tau2_native_bridge_accepts_alternative_tool_plan_with_same_state():
    adapter = _adapter(["exchange_item_alternative"])
    report = adapter.run(adapter.scenario_for("7"))

    assert report.passed is True
    calls = [
        event
        for event in report.trace.events
        if event.event_type is TraceEventType.TOOL_EXCHANGE
    ]
    assert calls[0].payload["name"] == "exchange_item_alternative"


def test_tau2_native_bridge_t2_kills_duplicate_that_native_db_accepts():
    adapter = _adapter(["exchange_item", "exchange_item"])

    report = adapter.run(adapter.scenario_for("7"))

    assert report.result.evaluation.achieved is True
    assert report.verdicts[0].passed is True
    assert report.verdicts[1].passed is True
    assert report.verdicts[2].passed is False
    effect_counts = report.verdicts[2].evidence["attempt_counts"]
    assert list(effect_counts.values()) == [2]


def test_tau2_native_bridge_reports_missing_required_state_change():
    adapter = _adapter([])

    report = adapter.run(adapter.scenario_for("7"))

    assert report.result.evaluation.achieved is False
    assert report.passed is False
    assert [verdict.passed for verdict in report.verdicts] == [False, False, False]


def test_tau2_state_derivation_ignores_failed_read_only_reference_action():
    raw_task = deepcopy(RAW_TASK)
    raw_task["evaluation_criteria"]["actions"].insert(
        0,
        {
            "action_id": "7-read-missing",
            "requestor": "assistant",
            "name": "read_missing_order",
            "arguments": {"order_id": "#missing"},
        },
    )
    adapter = _adapter(["exchange_item"], raw_task=raw_task)

    scenario = adapter.scenario_for("7")

    assert scenario.fixtures["tau2_t2"]["expected_state_delta"]
