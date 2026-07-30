from typing import Any, Dict, List, Optional

import pytest

from nanoharness.components.context.simple_context import SimpleContextManager
from nanoharness.components.evaluator.trace_evaluator import TraceEvaluator
from nanoharness.components.hooks.simple_hooks import SimpleHookManager
from nanoharness.components.state.json_store import JsonStateStore
from nanoharness.components.tools.dict_registry import DictToolRegistry
from nanoharness.core.engine import NanoEngine
from nanoharness.core.schema import EvaluationResult, LLMResponse, ToolCall
from nanoharness.testing import (
    ContextMessageDropOperator,
    DuplicateToolCallOperator,
    EvaluatorFlipOperator,
    HookSkipOperator,
    MutationApplication,
    MutationCampaignReport,
    MutationConfigurationError,
    MutationRunner,
    MutationStatus,
    OracleKind,
    OracleSpec,
    RecordingLLM,
    RecordingToolRegistry,
    Scenario,
    ScenarioRunner,
    StaleToolResultOperator,
    TerminatedAsSuccessOperator,
    ToolArgumentDropOperator,
)


class StaticLLM:
    def __init__(self, responses):
        self._responses = list(responses)
        self._index = 0

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        response = self._responses[self._index]
        self._index += 1
        return response


def _spec(kind, **parameters):
    return OracleSpec(kind=kind, parameters=parameters)


def _echo_scenario(*, include_result_oracle=True):
    oracles = [
        _spec(OracleKind.GOAL_ACHIEVEMENT, expected=True),
        _spec(OracleKind.LIFECYCLE),
        _spec(
            OracleKind.MODEL_MESSAGES,
            require_scenario_query=True,
            every_exchange=True,
        ),
        _spec(
            OracleKind.TOOL_CALLS,
            required=["echo"],
            max_counts={"echo": 1},
            required_arguments={"echo": ["text"]},
            expected_arguments={"echo": {"text": "hello"}},
        ),
    ]
    if include_result_oracle:
        oracles.append(
            _spec(OracleKind.TOOL_RESULTS, expected_last={"echo": "hello"})
        )
    return Scenario(
        scenario_id="echo-mutation",
        query="echo hello",
        oracles=oracles,
    )


def _echo_engine_factory(scenario, recorder):
    registry = DictToolRegistry()

    @registry.tool
    def echo(text: str):
        """Echo text."""
        return text

    llm = StaticLLM([
        LLMResponse(
            content="call echo",
            tool_calls=[ToolCall(name="echo", arguments={"text": "hello"})],
        ),
        LLMResponse(content="done"),
    ])
    return NanoEngine(
        llm_client=RecordingLLM(llm, recorder),
        tools=RecordingToolRegistry(registry, recorder),
        context=SimpleContextManager(),
        state=JsonStateStore("/tmp/mutation-echo.json"),
        hooks=SimpleHookManager(),
        evaluator=TraceEvaluator(),
    )


class TestMutationCampaign:
    def test_six_boundary_mutants_are_killed_by_composed_oracles(self):
        scenario = _echo_scenario()
        operators = [
            EvaluatorFlipOperator(),
            HookSkipOperator(),
            ContextMessageDropOperator(),
            ToolArgumentDropOperator("echo", "text"),
            StaleToolResultOperator("echo"),
            DuplicateToolCallOperator("echo"),
        ]

        campaign = MutationRunner(ScenarioRunner(_echo_engine_factory)).run(
            scenario,
            operators,
        )

        assert campaign.baseline.passed is True
        assert campaign.killed == 6
        assert campaign.survived == 0
        assert campaign.not_applicable == 0
        assert campaign.errors == 0
        assert campaign.mutation_score == 1.0
        assert all(
            outcome.status is MutationStatus.KILLED for outcome in campaign.outcomes
        )
        assert campaign.baseline.result.evaluation.achieved is True
        assert len([
            event for event in campaign.baseline.trace.events
            if event.payload.get("name") == "echo"
        ]) == 1

    def test_campaign_round_trips_through_json(self):
        campaign = MutationRunner(ScenarioRunner(_echo_engine_factory)).run(
            _echo_scenario(),
            [EvaluatorFlipOperator()],
        )

        restored = MutationCampaignReport.model_validate_json(
            campaign.model_dump_json()
        )

        assert restored == campaign

    def test_surviving_mutant_is_included_in_score(self):
        scenario = _echo_scenario(include_result_oracle=False)
        scenario.oracles = [
            oracle
            for oracle in scenario.oracles
            if oracle.kind != OracleKind.TOOL_CALLS.value
        ]

        campaign = MutationRunner(ScenarioRunner(_echo_engine_factory)).run(
            scenario,
            [StaleToolResultOperator("echo")],
        )

        assert campaign.outcomes[0].status is MutationStatus.SURVIVED
        assert campaign.mutation_score == 0.0

    def test_not_applicable_mutant_is_excluded_from_score(self):
        campaign = MutationRunner(ScenarioRunner(_echo_engine_factory)).run(
            _echo_scenario(),
            [TerminatedAsSuccessOperator(), EvaluatorFlipOperator()],
        )

        assert campaign.not_applicable == 1
        assert campaign.killed == 1
        assert campaign.mutation_score == 1.0

    def test_equivalent_mutant_is_classified_and_excluded_from_score(self):
        campaign = MutationRunner(ScenarioRunner(_echo_engine_factory)).run(
            _echo_scenario(),
            [
                StaleToolResultOperator("echo", replacement="hello"),
                EvaluatorFlipOperator(),
            ],
        )

        assert campaign.equivalent == 1
        assert campaign.killed == 1
        assert campaign.outcomes[0].status is MutationStatus.EQUIVALENT
        assert campaign.mutation_score == 1.0

    def test_invalid_mutant_is_classified_and_excluded_from_score(self):
        class InvalidOperator:
            operator_id = "invalid"
            kind = "invalid"

            def apply(self, scenario, baseline):
                return MutationApplication(
                    applied=True,
                    valid=False,
                    reason="generated an invalid mutant",
                )

        campaign = MutationRunner(ScenarioRunner(_echo_engine_factory)).run(
            _echo_scenario(),
            [InvalidOperator(), EvaluatorFlipOperator()],
        )

        assert campaign.invalid == 1
        assert campaign.killed == 1
        assert campaign.outcomes[0].status is MutationStatus.INVALID
        assert campaign.mutation_score == 1.0

    def test_baseline_failure_prevents_mutant_classification(self):
        scenario = _echo_scenario()
        scenario.oracles[0] = _spec(OracleKind.GOAL_ACHIEVEMENT, expected=False)

        campaign = MutationRunner(ScenarioRunner(_echo_engine_factory)).run(
            scenario,
            [EvaluatorFlipOperator()],
        )

        assert campaign.baseline.passed is False
        assert campaign.outcomes[0].status is MutationStatus.BASELINE_FAILED
        assert campaign.mutation_score is None

    def test_operator_exception_is_reported_without_aborting_campaign(self):
        class BrokenOperator:
            operator_id = "broken"
            kind = "broken"

            def apply(self, scenario, baseline):
                raise RuntimeError("mutation failed")

        campaign = MutationRunner(ScenarioRunner(_echo_engine_factory)).run(
            _echo_scenario(),
            [BrokenOperator(), EvaluatorFlipOperator()],
        )

        assert campaign.errors == 1
        assert campaign.killed == 1
        assert campaign.outcomes[0].error_type == "RuntimeError"

    def test_duplicate_operator_ids_are_rejected_before_baseline(self):
        factory_called = False

        def factory(scenario, recorder):
            nonlocal factory_called
            factory_called = True
            return _echo_engine_factory(scenario, recorder)

        with pytest.raises(MutationConfigurationError, match="Duplicate"):
            MutationRunner(ScenarioRunner(factory)).run(
                _echo_scenario(),
                [EvaluatorFlipOperator(), EvaluatorFlipOperator()],
            )

        assert factory_called is False


class TestHistoricalMutationOperators:
    def test_terminated_as_success_mutant_is_killed(self):
        class RejectingEvaluator(TraceEvaluator):
            def evaluate_success(self, query, trajectory):
                return EvaluationResult(
                    achieved=False,
                    confidence=1.0,
                    explanation="goal not achieved",
                )

        def factory(scenario, recorder):
            return NanoEngine(
                llm_client=RecordingLLM(
                    StaticLLM([LLMResponse(content="stopped without success")]),
                    recorder,
                ),
                tools=RecordingToolRegistry(DictToolRegistry(), recorder),
                context=SimpleContextManager(),
                state=JsonStateStore("/tmp/mutation-terminated.json"),
                hooks=SimpleHookManager(),
                evaluator=RejectingEvaluator(),
            )

        scenario = Scenario(
            scenario_id="terminated-is-not-success",
            query="complete real work",
            oracles=[
                _spec(OracleKind.GOAL_ACHIEVEMENT, expected=False),
                _spec(OracleKind.LIFECYCLE),
            ],
        )

        campaign = MutationRunner(ScenarioRunner(factory)).run(
            scenario,
            [TerminatedAsSuccessOperator()],
        )

        assert campaign.outcomes[0].status is MutationStatus.KILLED
        assert campaign.mutation_score == 1.0
