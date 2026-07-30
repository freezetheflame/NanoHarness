"""Serializable scenario and oracle-result models for deterministic agent tests."""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from nanoharness.core.schema import RunResult
from nanoharness.testing.trace import AgentTrace


SCENARIO_SCHEMA_VERSION = 1


class OracleKind(str, Enum):
    """Names of deterministic oracles included with NanoHarness."""

    GOAL_ACHIEVEMENT = "goal_achievement"
    RUN_STATUS = "run_status"
    STOP_REASON = "stop_reason"
    LIFECYCLE = "lifecycle"
    MODEL_MESSAGES = "model_messages"
    TOOL_CALLS = "tool_calls"
    TOOL_RESULTS = "tool_results"
    STATE_VALUES = "state_values"
    PERMISSION_ENFORCEMENT = "permission_enforcement"
    COMPONENT_ERRORS = "component_errors"
    EXECUTION_ERROR = "execution_error"


class OracleSeverity(str, Enum):
    """Severity retained in reports for later policy and CI integration."""

    ERROR = "error"
    WARNING = "warning"


class OracleSpec(BaseModel):
    """Serializable configuration for one deterministic test oracle."""

    oracle_id: Optional[str] = None
    kind: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    severity: OracleSeverity = OracleSeverity.ERROR


class Scenario(BaseModel):
    """A versioned agent test input with fixtures and expected invariants."""

    schema_version: int = Field(default=SCENARIO_SCHEMA_VERSION, ge=1)
    scenario_id: str = Field(min_length=1)
    query: str
    seed: Optional[int] = None
    tags: List[str] = Field(default_factory=list)
    fixtures: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    oracles: List[OracleSpec] = Field(default_factory=list)


class ExecutionError(BaseModel):
    """Normalized exception raised while building or running a scenario."""

    error_type: str
    message: str


class OracleVerdict(BaseModel):
    """Deterministic result of evaluating one oracle specification."""

    oracle_id: str
    kind: str
    passed: bool
    message: str
    severity: OracleSeverity = OracleSeverity.ERROR
    evidence: Dict[str, Any] = Field(default_factory=dict)


class ScenarioReport(BaseModel):
    """Complete execution and oracle report for one scenario."""

    schema_version: int = Field(default=SCENARIO_SCHEMA_VERSION, ge=1)
    scenario_id: str
    passed: bool
    result: Optional[RunResult] = None
    trace: AgentTrace
    execution_error: Optional[ExecutionError] = None
    verdicts: List[OracleVerdict] = Field(default_factory=list)

    def failure_messages(self) -> List[str]:
        return [verdict.message for verdict in self.verdicts if not verdict.passed]

    def raise_for_failure(self) -> None:
        if not self.passed:
            details = "; ".join(self.failure_messages()) or "scenario failed"
            raise ScenarioAssertionError(f"Scenario {self.scenario_id!r}: {details}")


class ScenarioAssertionError(AssertionError):
    """Raised by ``ScenarioReport.raise_for_failure`` for pytest-style use."""


class UnsupportedScenarioVersionError(ValueError):
    """Raised when no migration path exists for a scenario schema version."""
