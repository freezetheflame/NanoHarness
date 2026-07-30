from datetime import datetime, timezone
from enum import Enum

import pytest
from pydantic import BaseModel

from nanoharness.testing import (
    AgentDojoBenchmarkAdapter,
    BenchmarkConfigurationError,
    BenchmarkManifest,
    BenchmarkSourceIdentity,
)


class FakeEnvironment(BaseModel):
    account: str = "primary"
    initialized: bool = False


class FakeCall(BaseModel):
    function: str
    args: dict
    id: str | None = None
    placeholder_args: dict | None = None


class FakeDifficulty(Enum):
    EASY = 1


class FakeUserTask:
    ID = "user_task_1"
    PROMPT = "Pay the exact invoice."
    GROUND_TRUTH_OUTPUT = "Paid."
    DIFFICULTY = FakeDifficulty.EASY

    @staticmethod
    def init_environment(environment):
        environment.initialized = True
        return environment

    def ground_truth(self, pre_environment):
        assert pre_environment.initialized is True
        return [
            FakeCall(
                function="pay_invoice",
                args={"account": pre_environment.account, "amount": 12},
            )
        ]

    def utility(self, model_output, pre_environment, post_environment, strict=True):
        return True

    def utility_from_traces(
        self,
        model_output,
        pre_environment,
        post_environment,
        traces,
    ):
        return bool(traces)


class FakeSuite:
    user_tasks = {"user_task_1": FakeUserTask()}

    def load_and_inject_default_environment(self, injections):
        assert injections == {}
        return FakeEnvironment()


def _adapter():
    source = BenchmarkSourceIdentity(
        benchmark_id="agentdojo@test",
        distribution="agentdojo",
        package_version="test",
        revision="0123456789abcdef",
        source_url="https://github.com/ethz-spylab/agentdojo",
        license="MIT",
    )
    return AgentDojoBenchmarkAdapter(
        source,
        lambda benchmark_version, suite: FakeSuite(),
    )


def test_agentdojo_conversion_freezes_source_task_and_loss_boundary():
    frozen_at = datetime(2026, 7, 30, tzinfo=timezone.utc)

    manifest = _adapter().convert_user_tasks(
        "banking",
        ["user_task_1"],
        benchmark_version="v1.2.2",
        manifest_id="agentdojo-test-selection",
        frozen_at=frozen_at,
        metadata={"selection_role": "derivation"},
    )

    assert manifest.source.revision == "0123456789abcdef"
    assert manifest.conversion_scope == "offline_task_to_scenario"
    assert manifest.oracle_binding == "unbound"
    assert manifest.metadata["selection_role"] == "derivation"
    record = manifest.tasks[0]
    assert record.source_task_id == "user_task_1"
    assert record.reference_tool_calls == [
        {
            "function": "pay_invoice",
            "args": {"account": "primary", "amount": 12},
            "id": None,
            "placeholder_args": None,
        }
    ]
    assert record.scorer.trace_aware is True
    assert record.scenario.query == "Pay the exact invoice."
    assert record.scenario.oracles == []
    assert record.scenario.metadata["benchmark"]["oracle_binding"] == "unbound"
    assert record.scenario.fixtures["agentdojo_reference"]["semantics"] == (
        "non_normative_reference_plan"
    )
    manifest.assert_unchanged()
    round_trip = BenchmarkManifest.model_validate_json(
        manifest.model_dump_json()
    )
    assert round_trip == manifest


def test_agentdojo_conversion_rejects_implicit_duplicate_and_unknown_selection():
    adapter = _adapter()

    with pytest.raises(BenchmarkConfigurationError, match="at least one"):
        adapter.convert_user_tasks("banking", [])
    with pytest.raises(BenchmarkConfigurationError, match="duplicate"):
        adapter.convert_user_tasks(
            "banking",
            ["user_task_1", "user_task_1"],
        )
    with pytest.raises(BenchmarkConfigurationError, match="Unknown"):
        adapter.convert_user_tasks("banking", ["user_task_999"])


def test_benchmark_manifest_detects_post_freeze_scenario_drift():
    manifest = _adapter().convert_user_tasks(
        "banking",
        ["user_task_1"],
        frozen_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
    )
    manifest.tasks[0].scenario.query = "Changed after freezing"

    with pytest.raises(BenchmarkConfigurationError, match="changed"):
        manifest.assert_unchanged()


def test_agentdojo_adapter_rejects_non_agentdojo_identity():
    source = _adapter().source.model_copy(update={"distribution": "other"})

    with pytest.raises(ValueError, match="distribution"):
        AgentDojoBenchmarkAdapter(source, lambda version, suite: FakeSuite())
