"""Runtime-neutral contracts for executing subjects as NanoHarness Scenarios."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Dict, Optional, Protocol

from pydantic import BaseModel, Field, model_validator

from nanoharness.core.schema import RunResult
from nanoharness.testing.oracle import OracleEvaluator
from nanoharness.testing.runner import ScenarioRunner
from nanoharness.testing.scenario import (
    SCENARIO_SCHEMA_VERSION,
    ExecutionError,
    OracleSeverity,
    Scenario,
    ScenarioReport,
    UnsupportedScenarioVersionError,
)
from nanoharness.testing.trace import (
    TraceEventType,
    TraceRecorder,
    normalize_trace_value,
    redact_sensitive_fields,
)


class SubjectIdentity(BaseModel):
    """Immutable provenance expected for one experimental subject revision."""

    subject_id: str = Field(min_length=1)
    runtime: str = Field(min_length=1)
    version: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    source_url: Optional[str] = None
    independently_developed: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_external_provenance(self):
        if self.independently_developed and not self.source_url:
            raise ValueError(
                "Independently developed subjects require a stable source_url"
            )
        if self.independently_developed and self.revision.casefold() in {
            "head",
            "latest",
            "main",
            "master",
        }:
            raise ValueError(
                "Independently developed subjects require an immutable revision"
            )
        self.metadata = redact_sensitive_fields(normalize_trace_value(self.metadata))
        return self


class SubjectAdapter(Protocol):
    """Minimum interface consumed by the reproducible experiment runner."""

    @property
    def identity(self) -> SubjectIdentity:
        ...

    def run(self, scenario: Scenario) -> ScenarioReport:
        ...


class ExternalExecutor(Protocol):
    """External runtime execution that emits normalized events through a recorder."""

    def __call__(self, scenario: Scenario, recorder: TraceRecorder) -> RunResult:
        ...


class ScenarioRunnerAdapter:
    """Expose an existing NanoHarness ``ScenarioRunner`` as a subject."""

    def __init__(self, identity: SubjectIdentity, runner: ScenarioRunner):
        self._identity = SubjectIdentity.model_validate(identity.model_dump())
        self._runner = runner

    @property
    def identity(self) -> SubjectIdentity:
        return SubjectIdentity.model_validate(self._identity.model_dump())

    def run(self, scenario: Scenario) -> ScenarioReport:
        report = ScenarioReport.model_validate(
            self._runner.run(scenario).model_dump()
        )
        report.trace.metadata["scenario_id"] = scenario.scenario_id
        report.trace.metadata["seed"] = scenario.seed
        report.trace.metadata["subject"] = self._identity.model_dump(mode="json")
        return report


RecorderFactory = Callable[[Scenario, SubjectIdentity], TraceRecorder]


class CallableSubjectAdapter:
    """Adapt a recorder-aware external executor to deterministic Scenario Oracles."""

    def __init__(
        self,
        identity: SubjectIdentity,
        executor: ExternalExecutor,
        *,
        oracle_evaluator: Optional[OracleEvaluator] = None,
        recorder_factory: Optional[RecorderFactory] = None,
    ):
        self._identity = SubjectIdentity.model_validate(identity.model_dump())
        self._executor = executor
        self._oracles = oracle_evaluator or OracleEvaluator()
        self._recorder_factory = recorder_factory or self._default_recorder

    @property
    def identity(self) -> SubjectIdentity:
        return SubjectIdentity.model_validate(self._identity.model_dump())

    def run(self, scenario: Scenario) -> ScenarioReport:
        if scenario.schema_version != SCENARIO_SCHEMA_VERSION:
            raise UnsupportedScenarioVersionError(
                f"Scenario schema version {scenario.schema_version} is unsupported; "
                f"expected {SCENARIO_SCHEMA_VERSION}"
            )
        self._oracles.validate_specs(scenario)
        recorder = self._recorder_factory(scenario, self._identity)
        result = None
        execution_error = None
        recorder.record(
            TraceEventType.TASK_STARTED,
            {"query": scenario.query, "adapter_managed": True},
        )
        try:
            candidate = self._executor(scenario, recorder)
            if not isinstance(candidate, RunResult):
                raise TypeError(
                    "External subject executor must return a RunResult"
                )
            result = candidate
            recorder.record(
                TraceEventType.TASK_COMPLETED,
                {"result": result, "adapter_managed": True},
            )
        except Exception as exc:
            execution_error = ExecutionError(
                error_type=type(exc).__name__,
                message=str(exc),
            )
        trace = recorder.snapshot()
        trace.metadata["scenario_id"] = scenario.scenario_id
        trace.metadata["seed"] = scenario.seed
        trace.metadata["subject"] = self._identity.model_dump(mode="json")
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

    @staticmethod
    def _default_recorder(
        scenario: Scenario,
        identity: SubjectIdentity,
    ) -> TraceRecorder:
        return TraceRecorder(
            metadata={
                "scenario_id": scenario.scenario_id,
                "seed": scenario.seed,
                "tags": scenario.tags,
                "subject": identity.model_dump(mode="json"),
            }
        )
