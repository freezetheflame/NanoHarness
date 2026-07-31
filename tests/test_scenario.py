from typing import Any, Dict, List, Optional

import pytest
from pydantic import BaseModel

from nanoharness.components.context.simple_context import SimpleContextManager
from nanoharness.components.evaluator.trace_evaluator import TraceEvaluator
from nanoharness.components.hooks.simple_hooks import SimpleHookManager
from nanoharness.components.state.json_store import JsonStateStore
from nanoharness.components.tools.dict_registry import DictToolRegistry
from nanoharness.core.engine import NanoEngine
from nanoharness.core.schema import LLMResponse, RunStatus, StopReason, ToolCall
from nanoharness.testing import (
    OracleConfigurationError,
    OracleEvaluator,
    OracleKind,
    OracleSeverity,
    OracleSpec,
    OracleVerdict,
    RecordingLLM,
    RecordingToolRegistry,
    Scenario,
    ScenarioAssertionError,
    ScenarioReport,
    ScenarioRunner,
    TraceRecorder,
    UnsupportedScenarioVersionError,
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


def _plain_engine_factory(responses):
    def factory(scenario, recorder):
        return NanoEngine(
            llm_client=StaticLLM(responses),
            tools=DictToolRegistry(),
            context=SimpleContextManager(),
            state=JsonStateStore(f"/tmp/{scenario.scenario_id}.json"),
            hooks=SimpleHookManager(),
            evaluator=TraceEvaluator(),
        )

    return factory


def _spec(kind, **parameters):
    return OracleSpec(kind=kind, parameters=parameters)


class TestScenarioModels:
    def test_scenario_round_trips_through_json(self):
        scenario = Scenario(
            scenario_id="json-round-trip",
            query="do it",
            seed=42,
            tags=["unit"],
            fixtures={"account": {"balance": 10}},
            oracles=[_spec(OracleKind.GOAL_ACHIEVEMENT, expected=True)],
        )

        restored = Scenario.model_validate_json(scenario.model_dump_json())

        assert restored == scenario

    def test_report_round_trips_through_json(self):
        scenario = Scenario(scenario_id="report-json", query="finish")
        report = ScenarioRunner(
            _plain_engine_factory([LLMResponse(content="done")])
        ).run(scenario)

        restored = ScenarioReport.model_validate_json(report.model_dump_json())

        assert restored == report


class TestBuiltInOracles:
    def test_goal_status_stop_and_lifecycle_oracles_pass_together(self):
        scenario = Scenario(
            scenario_id="standard-oracles",
            query="finish",
            oracles=[
                _spec(OracleKind.GOAL_ACHIEVEMENT, expected=True),
                _spec(OracleKind.RUN_STATUS, allowed=[RunStatus.COMPLETED]),
                _spec(OracleKind.STOP_REASON, allowed=[StopReason.MODEL_TERMINATED]),
                _spec(OracleKind.LIFECYCLE),
            ],
        )

        report = ScenarioRunner(
            _plain_engine_factory([LLMResponse(content="done")])
        ).run(scenario)

        assert report.passed is True
        assert len(report.verdicts) == 4
        assert all(verdict.passed for verdict in report.verdicts)
        assert report.trace.metadata["scenario_id"] == "standard-oracles"

    def test_failed_oracle_produces_assertion_details(self):
        scenario = Scenario(
            scenario_id="failed-goal",
            query="finish",
            oracles=[_spec(OracleKind.GOAL_ACHIEVEMENT, expected=False)],
        )
        report = ScenarioRunner(
            _plain_engine_factory([LLMResponse(content="done")])
        ).run(scenario)

        assert report.passed is False
        with pytest.raises(ScenarioAssertionError, match="Expected goal achievement"):
            report.raise_for_failure()

    def test_warning_verdict_does_not_fail_scenario(self):
        scenario = Scenario(
            scenario_id="warning",
            query="finish",
            oracles=[
                OracleSpec(
                    kind=OracleKind.GOAL_ACHIEVEMENT,
                    parameters={"expected": False},
                    severity=OracleSeverity.WARNING,
                )
            ],
        )

        report = ScenarioRunner(
            _plain_engine_factory([LLMResponse(content="done")])
        ).run(scenario)

        assert report.passed is True
        assert report.verdicts[0].passed is False

    def test_tool_call_oracle_uses_recorded_boundaries(self):
        def factory(scenario, recorder):
            registry = DictToolRegistry()

            @registry.tool
            def echo(text: str):
                """Echo text."""
                return text

            llm = StaticLLM([
                LLMResponse(
                    content="echo",
                    tool_calls=[ToolCall(name="echo", arguments={"text": "hello"})],
                ),
                LLMResponse(content="done"),
            ])
            return NanoEngine(
                llm_client=RecordingLLM(llm, recorder),
                tools=RecordingToolRegistry(registry, recorder),
                context=SimpleContextManager(),
                state=JsonStateStore("/tmp/scenario-tools.json"),
                hooks=SimpleHookManager(),
                evaluator=TraceEvaluator(),
            )

        scenario = Scenario(
            scenario_id="tool-oracle",
            query="echo",
            oracles=[
                _spec(
                    OracleKind.TOOL_CALLS,
                    required=["echo"],
                    forbidden=["delete"],
                    min_counts={"echo": 1},
                    max_counts={"echo": 1},
                )
            ],
        )

        report = ScenarioRunner(factory).run(scenario)

        assert report.passed is True
        assert report.verdicts[0].evidence["counts"] == {"echo": 1}

    def test_state_delta_oracle_uses_semantic_live_state_projection(self):
        recorder = TraceRecorder()
        recorder.record_environment_delta(
            "retail-db",
            {
                "orders/#1/status": {"before": "delivered", "after": "exchanged"},
            },
        )
        scenario = Scenario(
            scenario_id="state-delta",
            query="exchange the item",
            oracles=[
                _spec(
                    OracleKind.STATE_DELTA,
                    scope="retail-db",
                    expected_changes={
                        "orders/#1/status": {
                            "before": "delivered",
                            "after": "exchanged",
                        }
                    },
                )
            ],
        )

        verdict = OracleEvaluator().evaluate(
            scenario,
            result=None,
            trace=recorder.snapshot(),
            execution_error=None,
        )[0]

        assert verdict.passed is True
        assert verdict.evidence["unexpected_changes"] == []

    def test_state_delta_oracle_rejects_unrelated_changes(self):
        recorder = TraceRecorder()
        recorder.record_environment_delta(
            "retail-db",
            {
                "orders/#1/status": {"before": "delivered", "after": "exchanged"},
                "users/42/balance": {"before": 100, "after": 0},
            },
        )
        scenario = Scenario(
            scenario_id="state-delta-unrelated",
            query="exchange the item",
            oracles=[
                _spec(
                    OracleKind.STATE_DELTA,
                    scope="retail-db",
                    expected_changes={
                        "orders/#1/status": {
                            "before": "delivered",
                            "after": "exchanged",
                        }
                    },
                )
            ],
        )

        verdict = OracleEvaluator().evaluate(
            scenario,
            result=None,
            trace=recorder.snapshot(),
            execution_error=None,
        )[0]

        assert verdict.passed is False
        assert verdict.evidence["unexpected_changes"] == ["users/42/balance"]

    def test_side_effect_oracle_counts_real_attempts_not_tool_sequence(self):
        recorder = TraceRecorder()
        recorder.record_side_effect(
            "exchange-order-1",
            "attempt-1",
            outcome="committed",
            attributes={"order_id": "#1"},
        )
        scenario = Scenario(
            scenario_id="side-effect-ledger",
            query="exchange the item",
            oracles=[
                _spec(
                    OracleKind.SIDE_EFFECTS,
                    expected_attempts={"exchange-order-1": 1},
                    expected_commits={"exchange-order-1": 1},
                    expected_attributes={"exchange-order-1": {"order_id": "#1"}},
                )
            ],
        )
        evaluator = OracleEvaluator()

        baseline = evaluator.evaluate(
            scenario,
            result=None,
            trace=recorder.snapshot(),
            execution_error=None,
        )[0]
        recorder.record_side_effect(
            "exchange-order-1",
            "attempt-2",
            outcome="committed",
            attributes={"order_id": "#1"},
        )
        duplicate = evaluator.evaluate(
            scenario,
            result=None,
            trace=recorder.snapshot(),
            execution_error=None,
        )[0]

        assert baseline.passed is True
        assert duplicate.passed is False
        assert duplicate.evidence["attempt_counts"] == {"exchange-order-1": 2}
        assert duplicate.evidence["commit_counts"] == {"exchange-order-1": 2}

    def test_side_effect_oracle_rejects_duplicate_attempt_ids(self):
        recorder = TraceRecorder()
        recorder.record_side_effect(
            "exchange-order-1",
            "reused-attempt",
            outcome="committed",
        )
        recorder.record_side_effect(
            "notify-customer-1",
            "reused-attempt",
            outcome="committed",
        )
        scenario = Scenario(
            scenario_id="side-effect-attempt-ids",
            query="exchange and notify",
            oracles=[
                _spec(
                    OracleKind.SIDE_EFFECTS,
                    expected_commits={
                        "exchange-order-1": 1,
                        "notify-customer-1": 1,
                    },
                )
            ],
        )

        verdict = OracleEvaluator().evaluate(
            scenario,
            result=None,
            trace=recorder.snapshot(),
            execution_error=None,
        )[0]

        assert verdict.passed is False
        assert verdict.evidence["duplicate_attempt_ids"] == ["reused-attempt"]

    @pytest.mark.parametrize("allowed, expected_pass", [([], False), (["tool"], True)])
    def test_component_error_oracle_can_forbid_or_allow_failures(
        self,
        allowed,
        expected_pass,
    ):
        def factory(scenario, recorder):
            registry = DictToolRegistry()

            @registry.tool
            def fail():
                """Fail."""
                raise RuntimeError("boom")

            llm = StaticLLM([
                LLMResponse(
                    content="fail",
                    tool_calls=[ToolCall(name="fail", arguments={})],
                ),
                LLMResponse(content="done"),
            ])
            return NanoEngine(
                llm_client=RecordingLLM(llm, recorder),
                tools=RecordingToolRegistry(registry, recorder),
                context=SimpleContextManager(),
                state=JsonStateStore("/tmp/scenario-errors.json"),
                hooks=SimpleHookManager(),
                evaluator=TraceEvaluator(),
            )

        scenario = Scenario(
            scenario_id=f"component-error-{expected_pass}",
            query="fail",
            oracles=[
                _spec(OracleKind.COMPONENT_ERRORS, allowed_components=allowed)
            ],
        )

        report = ScenarioRunner(factory).run(scenario)

        assert report.passed is expected_pass


class TestScenarioExecutionErrors:
    def test_expected_engine_factory_error_can_pass(self):
        def factory(scenario, recorder):
            raise RuntimeError("cannot build engine")

        scenario = Scenario(
            scenario_id="expected-error",
            query="fail",
            oracles=[
                _spec(
                    OracleKind.EXECUTION_ERROR,
                    should_error=True,
                    error_type="RuntimeError",
                    message_contains="build engine",
                )
            ],
        )

        report = ScenarioRunner(factory).run(scenario)

        assert report.passed is True
        assert report.result is None
        assert report.execution_error.error_type == "RuntimeError"

    def test_unexpected_execution_error_gets_implicit_failure(self):
        def factory(scenario, recorder):
            raise RuntimeError("unexpected")

        report = ScenarioRunner(factory).run(
            Scenario(scenario_id="unexpected-error", query="fail")
        )

        assert report.passed is False
        assert report.verdicts[0].oracle_id == "implicit:unexpected_execution_error"


class TestOracleConfiguration:
    def test_invalid_configuration_is_rejected_before_execution(self):
        factory_called = False

        def factory(scenario, recorder):
            nonlocal factory_called
            factory_called = True
            raise AssertionError("must not execute")

        scenario = Scenario(
            scenario_id="invalid-config",
            query="no-op",
            oracles=[_spec(OracleKind.RUN_STATUS, allowed=[])],
        )

        with pytest.raises(OracleConfigurationError, match="Invalid parameters"):
            ScenarioRunner(factory).run(scenario)

        assert factory_called is False

    def test_conflicting_tool_constraints_are_rejected(self):
        scenario = Scenario(
            scenario_id="conflict",
            query="no-op",
            oracles=[
                _spec(
                    OracleKind.TOOL_CALLS,
                    required=["write"],
                    forbidden=["write"],
                )
            ],
        )

        with pytest.raises(OracleConfigurationError, match="required and forbidden"):
            OracleEvaluator().validate_specs(scenario)

    def test_custom_oracle_can_be_registered(self):
        class Parameters(BaseModel):
            expected_query: str

        def evaluate(spec, parameters, context, oracle_id):
            passed = context.scenario.query == parameters.expected_query
            return OracleVerdict(
                oracle_id=oracle_id,
                kind=spec.kind,
                passed=passed,
                message="custom query check",
                severity=spec.severity,
            )

        evaluator = OracleEvaluator()
        evaluator.register("custom_query", Parameters, evaluate)
        scenario = Scenario(
            scenario_id="custom",
            query="expected",
            oracles=[
                OracleSpec(
                    oracle_id="query-check",
                    kind="custom_query",
                    parameters={"expected_query": "expected"},
                )
            ],
        )

        report = ScenarioRunner(
            _plain_engine_factory([LLMResponse(content="done")]),
            oracle_evaluator=evaluator,
        ).run(scenario)

        assert report.passed is True
        assert report.verdicts[0].oracle_id == "query-check"

    def test_future_scenario_version_is_rejected(self):
        scenario = Scenario(
            schema_version=2,
            scenario_id="future",
            query="no-op",
        )

        with pytest.raises(UnsupportedScenarioVersionError, match="version 2"):
            ScenarioRunner(
                _plain_engine_factory([LLMResponse(content="done")])
            ).run(scenario)
