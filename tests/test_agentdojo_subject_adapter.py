from datetime import datetime, timezone
from enum import Enum

import pytest
from pydantic import BaseModel

from nanoharness.testing import (
    AGENTDOJO_UTILITY_BINDING,
    AgentDojoBenchmarkAdapter,
    AgentDojoSubjectAdapter,
    BenchmarkConfigurationError,
    BenchmarkSourceIdentity,
    SubjectIdentity,
    TraceEventType,
)


class FakeEnvironment(BaseModel):
    balance: int = 20
    paid: bool = False


class FakeCall(BaseModel):
    function: str
    args: dict
    id: str | None = None
    placeholder_args: dict | None = None


class FakeDifficulty(Enum):
    EASY = 1


class FakeUserTask:
    PROMPT = "Pay twelve."
    GROUND_TRUTH_OUTPUT = "Paid."
    DIFFICULTY = FakeDifficulty.EASY

    @staticmethod
    def init_environment(environment):
        return environment

    def ground_truth(self, pre_environment):
        return [FakeCall(function="pay", args={"amount": 12})]

    def utility_from_traces(
        self,
        model_output,
        pre_environment,
        post_environment,
        traces,
    ):
        return None

    def utility(
        self,
        model_output,
        pre_environment,
        post_environment,
        strict=True,
    ):
        return strict and post_environment.paid and model_output == "Paid."


class FakeTool:
    name = "pay"
    description = "Pay an amount"

    class parameters:
        @staticmethod
        def model_json_schema():
            return {"type": "object"}


class FakeSuite:
    user_tasks = {"user_task_1": FakeUserTask()}
    tools = [FakeTool()]

    def load_and_inject_default_environment(self, injections):
        assert injections == {}
        return FakeEnvironment()


class NeverAbort(Exception):
    pass


class FakePipeline:
    def __init__(self, *, succeeds=True):
        self.succeeds = succeeds

    def query(self, query, runtime, env):
        call = FakeCall(function="pay", args={"amount": 12})
        if self.succeeds:
            env.balance -= 12
            env.paid = True
            answer = "Paid."
        else:
            answer = "Skipped."
        messages = [
            {"role": "assistant", "content": None, "tool_calls": [call]},
            {
                "role": "tool",
                "content": [{"type": "text", "content": "ok"}],
                "tool_call": call,
                "tool_call_id": None,
                "error": None,
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "content": answer}],
                "tool_calls": None,
            },
        ]
        return query, runtime, env, messages, {}


class RetryPipeline:
    def __init__(self):
        self.attempts = 0

    def query(self, query, runtime, env):
        self.attempts += 1
        if self.attempts == 1:
            return (
                query,
                runtime,
                env,
                [{"role": "assistant", "content": None, "tool_calls": []}],
                {},
            )
        return FakePipeline().query(query, runtime, env)


def _text(blocks):
    return "\n".join(block["content"] for block in blocks)


def _benchmark_manifest():
    source = BenchmarkSourceIdentity(
        benchmark_id="agentdojo@test",
        distribution="agentdojo",
        package_version="test",
        revision="0123456789abcdef",
        source_url="https://github.com/ethz-spylab/agentdojo",
        license="MIT",
    )
    converter = AgentDojoBenchmarkAdapter(
        source,
        lambda version, suite: FakeSuite(),
    )
    return converter.convert_user_tasks(
        "banking",
        ["user_task_1"],
        frozen_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
    )


def _identity():
    return SubjectIdentity(
        subject_id="fake-agentdojo-subject",
        runtime="agentdojo",
        version="test",
        revision="subject-revision",
    )


def _subject(*, succeeds=True):
    return AgentDojoSubjectAdapter(
        _identity(),
        _benchmark_manifest(),
        lambda scenario, task: FakePipeline(succeeds=succeeds),
        lambda version, suite: FakeSuite(),
        lambda tools, recorder: object(),
        NeverAbort,
        _text,
    )


def test_agentdojo_subject_binds_and_executes_original_utility():
    adapter = _subject()
    scenario = adapter.scenario_for("user_task_1", seed=7)

    report = adapter.run(scenario)

    assert scenario.metadata["benchmark"]["oracle_binding"] == (
        AGENTDOJO_UTILITY_BINDING
    )
    assert scenario.oracles[0].oracle_id == "agentdojo-original-utility"
    assert report.passed is True
    assert report.result.evaluation.achieved is True
    assert report.result.final_answer == "Paid."
    scored = [
        event
        for event in report.trace.events
        if event.event_type is TraceEventType.CUSTOM
        and event.payload.get("adapter_event") == "utility_scored"
    ]
    assert scored[0].payload["scorer_path"] == "utility"
    assert scored[0].payload["scorer_trace_scope"] == "final_attempt"
    exchanges = [
        event
        for event in report.trace.events
        if event.event_type is TraceEventType.TOOL_EXCHANGE
    ]
    assert exchanges[0].payload["name"] == "pay"
    assert exchanges[0].payload["arguments"] == {"amount": 12}


def test_agentdojo_subject_utility_failure_fails_bound_oracle():
    adapter = _subject(succeeds=False)

    report = adapter.run(adapter.scenario_for("user_task_1"))

    assert report.result.evaluation.achieved is False
    assert report.passed is False
    assert report.verdicts[0].passed is False


def test_agentdojo_subject_records_all_attempts_but_scores_final_trace():
    pipeline = RetryPipeline()
    adapter = AgentDojoSubjectAdapter(
        _identity(),
        _benchmark_manifest(),
        lambda scenario, task: pipeline,
        lambda version, suite: FakeSuite(),
        lambda tools, recorder: object(),
        NeverAbort,
        _text,
    )

    report = adapter.run(adapter.scenario_for("user_task_1"))

    scored = next(
        event
        for event in report.trace.events
        if event.payload.get("adapter_event") == "utility_scored"
    )
    assert report.passed is True
    assert scored.payload["attempts"] == 2
    assert len(scored.payload["function_trace"]) == 1
    assert scored.payload["function_trace"][0]["function"] == "pay"
    exchanges = [
        event
        for event in report.trace.events
        if event.event_type is TraceEventType.MODEL_EXCHANGE
    ]
    assert [event.payload["attempt"] for event in exchanges] == [1, 2, 2]


def test_agentdojo_subject_reports_pre_environment_drift_before_pipeline():
    class DriftSuite(FakeSuite):
        def load_and_inject_default_environment(self, injections):
            return FakeEnvironment(balance=21)

    adapter = AgentDojoSubjectAdapter(
        _identity(),
        _benchmark_manifest(),
        lambda scenario, task: FakePipeline(),
        lambda version, suite: DriftSuite(),
        lambda tools, recorder: object(),
        NeverAbort,
        _text,
    )

    report = adapter.run(adapter.scenario_for("user_task_1"))

    assert report.passed is False
    assert report.result is None
    assert report.execution_error.error_type == "BenchmarkConfigurationError"
    assert "pre-environment drift" in report.execution_error.message


def test_agentdojo_subject_rejects_tampered_or_unselected_scenario():
    adapter = _subject()
    scenario = adapter.scenario_for("user_task_1")
    scenario.query = "Different query"

    with pytest.raises(BenchmarkConfigurationError, match="does not match"):
        adapter.run(scenario)
    with pytest.raises(BenchmarkConfigurationError, match="absent"):
        adapter.scenario_for("user_task_999")


def test_agentdojo_subject_requires_runtime_and_matching_source():
    manifest = _benchmark_manifest()
    wrong_runtime = _identity().model_copy(update={"runtime": "other"})
    wrong_source = manifest.source.model_copy(update={"revision": "different"})

    with pytest.raises(ValueError, match="runtime"):
        AgentDojoSubjectAdapter(
            wrong_runtime,
            manifest,
            lambda scenario, task: FakePipeline(),
            lambda version, suite: FakeSuite(),
            lambda tools, recorder: object(),
            NeverAbort,
            _text,
        )
    with pytest.raises(BenchmarkConfigurationError, match="does not match"):
        AgentDojoSubjectAdapter(
            _identity(),
            manifest,
            lambda scenario, task: FakePipeline(),
            lambda version, suite: FakeSuite(),
            lambda tools, recorder: object(),
            NeverAbort,
            _text,
            installed_source=wrong_source,
        )
