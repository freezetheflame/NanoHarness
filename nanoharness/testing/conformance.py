"""Versioned semantic projections for cross-runtime conformance evidence."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from nanoharness.testing.scenario import Scenario, ScenarioReport


RUNTIME_CONFORMANCE_SCHEMA_VERSION = 1
RUNTIME_CONFORMANCE_CASE_IDS = frozenset({"M1", "M2", "M3", "M4"})
RUNTIME_CONFORMANCE_RUNTIMES = frozenset({"nanoharness", "langgraph"})


class ToolAttemptProjection(BaseModel):
    """One normalized tool-boundary attempt."""

    name: str = Field(min_length=1)
    arguments: Dict[str, Any] = Field(default_factory=dict)
    outcome: Literal["success", "error"]
    result: Any = None
    error_type: Optional[str] = None
    observation_delivered: bool = False

    @model_validator(mode="after")
    def validate_outcome(self):
        if self.outcome == "error" and not self.error_type:
            raise ValueError("error outcome requires error_type")
        if self.outcome == "success" and self.error_type is not None:
            raise ValueError("success outcome cannot contain error_type")
        return self


class PermissionDecisionProjection(BaseModel):
    """One normalized permission-boundary decision."""

    tool_name: str = Field(min_length=1)
    arguments: Dict[str, Any] = Field(default_factory=dict)
    allowed: bool
    denial: Optional[str] = None


class SemanticProjection(BaseModel):
    """Runtime-neutral facts retained from one ScenarioReport."""

    schema_version: int = RUNTIME_CONFORMANCE_SCHEMA_VERSION
    case_id: str = Field(min_length=1)
    runtime: str = Field(min_length=1)
    run_status: Optional[str] = None
    stop_reason: Optional[str] = None
    goal_achieved: Optional[bool] = None
    report_passed: bool
    execution_error_type: Optional[str] = None
    tool_attempts: List[ToolAttemptProjection] = Field(default_factory=list)
    permission_decisions: List[PermissionDecisionProjection] = Field(
        default_factory=list
    )
    recovery_observed: bool = False
    lifecycle_start_count: int = Field(ge=0)
    lifecycle_end_count: int = Field(ge=0)
    lifecycle_paired: bool
    event_ids_unique: bool
    sequences_monotonic: bool
    trace_ids_consistent: bool
    scenario_round_trip: bool
    report_round_trip: bool


class ProjectionMismatch(BaseModel):
    """One field-level disagreement retained for audit."""

    path: str = Field(min_length=1)
    expected: Any = None
    nanoharness: Any = None
    langgraph: Any = None


class CaseConformanceResult(BaseModel):
    """Paired result for one M1--M4 case."""

    case_id: str = Field(min_length=1)
    passed: bool
    nanoharness: SemanticProjection
    langgraph: SemanticProjection
    mismatches: List[ProjectionMismatch] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_comparison(self):
        if self.passed and self.mismatches:
            raise ValueError("passing comparison cannot contain mismatches")
        if self.nanoharness.case_id != self.case_id:
            raise ValueError("NanoHarness projection case_id does not match")
        if self.langgraph.case_id != self.case_id:
            raise ValueError("LangGraph projection case_id does not match")
        if self.nanoharness.runtime != "nanoharness":
            raise ValueError("NanoHarness projection runtime does not match")
        if self.langgraph.runtime != "langgraph":
            raise ValueError("LangGraph projection runtime does not match")
        return self


class RuntimeCaseEvidence(BaseModel):
    """Unmodified runtime report paired with its normalized projection."""

    case_id: str = Field(min_length=1)
    runtime: str = Field(min_length=1)
    scenario: Scenario
    report: ScenarioReport
    projection: SemanticProjection

    @model_validator(mode="after")
    def validate_identity(self):
        if self.scenario.scenario_id != self.case_id:
            raise ValueError("Scenario case_id does not match evidence")
        if self.report.scenario_id != self.case_id:
            raise ValueError("ScenarioReport case_id does not match evidence")
        if self.projection.case_id != self.case_id:
            raise ValueError("projection case_id does not match evidence")
        if self.projection.runtime != self.runtime:
            raise ValueError("projection runtime does not match evidence")
        return self


class RuntimeConformanceReport(BaseModel):
    """Auditable eight-cell M1--M4 cross-runtime experiment report."""

    schema_version: int = RUNTIME_CONFORMANCE_SCHEMA_VERSION
    experiment_id: str = Field(min_length=1)
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    runtime_versions: Dict[str, str]
    started_at: datetime
    finished_at: datetime
    cells: List[RuntimeCaseEvidence]
    comparisons: List[CaseConformanceResult]
    infrastructure_errors: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_report(self):
        if self.started_at.utcoffset() is None or self.finished_at.utcoffset() is None:
            raise ValueError("conformance timestamps require timezone offsets")
        keys = [(cell.case_id, cell.runtime) for cell in self.cells]
        if len(keys) != len(set(keys)):
            raise ValueError("runtime/case cells must be unique")
        required = {
            (case_id, runtime)
            for case_id in RUNTIME_CONFORMANCE_CASE_IDS
            for runtime in RUNTIME_CONFORMANCE_RUNTIMES
        }
        if set(keys) != required:
            raise ValueError("conformance report requires M1--M4 for both runtimes")
        comparison_ids = [item.case_id for item in self.comparisons]
        if len(comparison_ids) != len(set(comparison_ids)):
            raise ValueError("case comparisons must be unique")
        if set(comparison_ids) != RUNTIME_CONFORMANCE_CASE_IDS:
            raise ValueError("conformance report requires M1--M4 comparisons")
        return self
