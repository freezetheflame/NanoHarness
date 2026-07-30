"""Scenario execution that combines NanoEngine runs with deterministic oracles."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import List, Optional

from nanoharness.core.engine import NanoEngine
from nanoharness.testing.oracle import OracleEvaluator
from nanoharness.testing.scenario import (
    SCENARIO_SCHEMA_VERSION,
    ExecutionError,
    OracleSeverity,
    Scenario,
    ScenarioReport,
    UnsupportedScenarioVersionError,
)
from nanoharness.testing.trace import TraceRecorder


EngineFactory = Callable[[Scenario, TraceRecorder], NanoEngine]
RecorderFactory = Callable[[Scenario], TraceRecorder]


class ScenarioRunner:
    """Runs fresh engines and evaluates serializable deterministic oracles."""

    def __init__(
        self,
        engine_factory: EngineFactory,
        *,
        oracle_evaluator: Optional[OracleEvaluator] = None,
        recorder_factory: Optional[RecorderFactory] = None,
    ):
        self._engine_factory = engine_factory
        self._oracles = oracle_evaluator or OracleEvaluator()
        self._recorder_factory = recorder_factory or self._default_recorder

    @property
    def oracle_evaluator(self) -> OracleEvaluator:
        return self._oracles

    def run(self, scenario: Scenario) -> ScenarioReport:
        if scenario.schema_version != SCENARIO_SCHEMA_VERSION:
            raise UnsupportedScenarioVersionError(
                f"Scenario schema version {scenario.schema_version} is unsupported; "
                f"expected {SCENARIO_SCHEMA_VERSION}"
            )
        self._oracles.validate_specs(scenario)
        recorder = self._recorder_factory(scenario)
        result = None
        execution_error = None
        try:
            engine = self._engine_factory(scenario, recorder)
            recorder.attach(engine.hooks)
            result = engine.run(scenario.query)
        except Exception as exc:
            execution_error = ExecutionError(
                error_type=type(exc).__name__,
                message=str(exc),
            )
        trace = recorder.snapshot()
        verdicts = self._oracles.evaluate(
            scenario,
            result=result,
            trace=trace,
            execution_error=execution_error,
        )
        return ScenarioReport(
            scenario_id=scenario.scenario_id,
            passed=all(
                verdict.passed or verdict.severity is OracleSeverity.WARNING
                for verdict in verdicts
            ),
            result=result,
            trace=trace,
            execution_error=execution_error,
            verdicts=verdicts,
        )

    def run_many(self, scenarios: Iterable[Scenario]) -> List[ScenarioReport]:
        return [self.run(scenario) for scenario in scenarios]

    @staticmethod
    def _default_recorder(scenario: Scenario) -> TraceRecorder:
        return TraceRecorder(
            metadata={
                "scenario_id": scenario.scenario_id,
                "seed": scenario.seed,
                "tags": scenario.tags,
            }
        )
