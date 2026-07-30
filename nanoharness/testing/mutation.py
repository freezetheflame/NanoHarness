"""Trace-level mutation operators and mutation-score campaign execution."""

from __future__ import annotations

from enum import Enum
from typing import Any, List, Optional, Protocol, Sequence

from pydantic import BaseModel, Field

from nanoharness.testing.oracle import OracleEvaluator
from nanoharness.testing.runner import ScenarioRunner
from nanoharness.testing.scenario import (
    OracleSeverity,
    OracleVerdict,
    Scenario,
    ScenarioReport,
)
from nanoharness.testing.trace import TraceEvent, TraceEventType


MUTATION_SCHEMA_VERSION = 1


class MutationKind(str, Enum):
    """Initial agent-specific mutation categories."""

    HOOK_SKIP = "hook_skip"
    EVALUATOR_FLIP = "evaluator_flip"
    TERMINATED_AS_SUCCESS = "terminated_as_success"
    CONTEXT_MESSAGE_DROP = "context_message_drop"
    TOOL_ARGUMENT_DROP = "tool_argument_drop"
    TOOL_RESULT_STALE = "tool_result_stale"
    TOOL_CALL_DUPLICATE = "tool_call_duplicate"


class MutationStatus(str, Enum):
    """Outcome categories used in mutation score calculation."""

    KILLED = "killed"
    SURVIVED = "survived"
    NOT_APPLICABLE = "not_applicable"
    EQUIVALENT = "equivalent"
    INVALID = "invalid"
    ERROR = "error"
    BASELINE_FAILED = "baseline_failed"


class MutationApplication(BaseModel):
    """Result of applying one operator to a detached baseline report."""

    applied: bool
    reason: str
    valid: bool = True
    equivalent: bool = False
    report: Optional[ScenarioReport] = None
    target_event_ids: List[str] = Field(default_factory=list)


class MutationOutcome(BaseModel):
    """Serializable classification and oracle evidence for one mutant."""

    operator_id: str
    kind: str
    status: MutationStatus
    message: str
    target_event_ids: List[str] = Field(default_factory=list)
    failed_oracle_ids: List[str] = Field(default_factory=list)
    verdicts: List[OracleVerdict] = Field(default_factory=list)
    error_type: Optional[str] = None


class MutationCampaignReport(BaseModel):
    """Baseline plus mutation outcomes and a correctly scoped mutation score."""

    schema_version: int = Field(default=MUTATION_SCHEMA_VERSION, ge=1)
    scenario_id: str
    baseline: ScenarioReport
    outcomes: List[MutationOutcome] = Field(default_factory=list)
    killed: int = 0
    survived: int = 0
    not_applicable: int = 0
    equivalent: int = 0
    invalid: int = 0
    errors: int = 0
    mutation_score: Optional[float] = None


class MutationConfigurationError(ValueError):
    """Raised before execution when an operator campaign is ambiguous."""


class MutationOperator(Protocol):
    operator_id: str
    kind: str

    def apply(
        self,
        scenario: Scenario,
        baseline: ScenarioReport,
    ) -> MutationApplication:
        ...


def _renumber(events: Sequence[TraceEvent]) -> None:
    for sequence, event in enumerate(events):
        event.sequence = sequence


def _select(items: Sequence[Any], occurrence: int) -> Optional[Any]:
    try:
        return items[occurrence]
    except IndexError:
        return None


class HookSkipOperator:
    kind = MutationKind.HOOK_SKIP.value

    def __init__(
        self,
        event_type: TraceEventType = TraceEventType.TASK_COMPLETED,
        *,
        occurrence: int = 0,
        operator_id: Optional[str] = None,
    ):
        self.event_type = event_type
        self.occurrence = occurrence
        self.operator_id = operator_id or f"hook_skip:{event_type.value}:{occurrence}"

    def apply(self, scenario, baseline):
        matches = [
            event for event in baseline.trace.events
            if event.event_type is self.event_type
        ]
        target = _select(matches, self.occurrence)
        if target is None:
            return MutationApplication(
                applied=False,
                reason=f"No {self.event_type.value} event at occurrence {self.occurrence}",
            )
        baseline.trace.events = [
            event for event in baseline.trace.events if event.event_id != target.event_id
        ]
        _renumber(baseline.trace.events)
        return MutationApplication(
            applied=True,
            reason=f"Removed {self.event_type.value} event",
            report=baseline,
            target_event_ids=[target.event_id],
        )


class EvaluatorFlipOperator:
    kind = MutationKind.EVALUATOR_FLIP.value

    def __init__(self, *, operator_id: str = "evaluator_flip"):
        self.operator_id = operator_id

    def apply(self, scenario, baseline):
        if baseline.result is None:
            return MutationApplication(applied=False, reason="Run result is absent")
        baseline.result.evaluation.achieved = not baseline.result.evaluation.achieved
        baseline.result.evaluation.explanation = (
            f"[mutated:{self.operator_id}] "
            f"{baseline.result.evaluation.explanation}"
        )
        return MutationApplication(
            applied=True,
            reason="Flipped evaluation.achieved",
            report=baseline,
        )


class TerminatedAsSuccessOperator:
    kind = MutationKind.TERMINATED_AS_SUCCESS.value

    def __init__(self, *, operator_id: str = "terminated_as_success"):
        self.operator_id = operator_id

    def apply(self, scenario, baseline):
        if baseline.result is None:
            return MutationApplication(applied=False, reason="Run result is absent")
        terminated = any(
            step.status == "terminated" for step in baseline.result.trajectory
        )
        if not terminated:
            return MutationApplication(applied=False, reason="No terminated step exists")
        if baseline.result.evaluation.achieved:
            return MutationApplication(
                applied=False,
                reason="Result already reports goal achievement",
            )
        baseline.result.evaluation.achieved = True
        baseline.result.evaluation.explanation = (
            "[mutated:terminated_as_success] termination treated as success"
        )
        return MutationApplication(
            applied=True,
            reason="Changed a terminated unsuccessful run to achieved",
            report=baseline,
        )


class ContextMessageDropOperator:
    kind = MutationKind.CONTEXT_MESSAGE_DROP.value

    def __init__(
        self,
        *,
        occurrence: int = 0,
        operator_id: Optional[str] = None,
    ):
        self.occurrence = occurrence
        self.operator_id = operator_id or f"context_message_drop:{occurrence}"

    def apply(self, scenario, baseline):
        exchanges = [
            event for event in baseline.trace.events
            if event.event_type is TraceEventType.MODEL_EXCHANGE
        ]
        target = _select(exchanges, self.occurrence)
        if target is None:
            return MutationApplication(
                applied=False,
                reason=f"No model exchange at occurrence {self.occurrence}",
            )
        messages = target.payload.get("messages") or []
        matching_indexes = [
            index
            for index, message in enumerate(messages)
            if isinstance(message, dict)
            and message.get("role") == "user"
            and scenario.query in str(message.get("content", ""))
        ]
        if not matching_indexes:
            return MutationApplication(
                applied=False,
                reason="No user message contains the scenario query",
            )
        del messages[matching_indexes[0]]
        target.payload["messages"] = messages
        return MutationApplication(
            applied=True,
            reason="Dropped the scenario query from one model request",
            report=baseline,
            target_event_ids=[target.event_id],
        )


class ToolArgumentDropOperator:
    kind = MutationKind.TOOL_ARGUMENT_DROP.value

    def __init__(
        self,
        tool_name: str,
        argument: str,
        *,
        occurrence: int = 0,
        operator_id: Optional[str] = None,
    ):
        self.tool_name = tool_name
        self.argument = argument
        self.occurrence = occurrence
        self.operator_id = operator_id or (
            f"tool_argument_drop:{tool_name}:{argument}:{occurrence}"
        )

    def apply(self, scenario, baseline):
        calls = [
            event
            for event in baseline.trace.events
            if event.event_type in {
                TraceEventType.TOOL_EXCHANGE,
                TraceEventType.TOOL_ERROR,
            }
            and event.payload.get("name") == self.tool_name
        ]
        target = _select(calls, self.occurrence)
        if target is None:
            return MutationApplication(
                applied=False,
                reason=f"No call to tool {self.tool_name!r} at requested occurrence",
            )
        arguments = target.payload.get("arguments") or {}
        if self.argument not in arguments:
            return MutationApplication(
                applied=False,
                reason=f"Argument {self.argument!r} is already absent",
            )
        del arguments[self.argument]
        target.payload["arguments"] = arguments
        return MutationApplication(
            applied=True,
            reason=f"Dropped argument {self.argument!r} from {self.tool_name!r}",
            report=baseline,
            target_event_ids=[target.event_id],
        )


class StaleToolResultOperator:
    kind = MutationKind.TOOL_RESULT_STALE.value

    def __init__(
        self,
        tool_name: str,
        replacement: Any = "[STALE]",
        *,
        occurrence: int = -1,
        operator_id: Optional[str] = None,
    ):
        self.tool_name = tool_name
        self.replacement = replacement
        self.occurrence = occurrence
        self.operator_id = operator_id or f"tool_result_stale:{tool_name}:{occurrence}"

    def apply(self, scenario, baseline):
        calls = [
            event
            for event in baseline.trace.events
            if event.event_type is TraceEventType.TOOL_EXCHANGE
            and event.payload.get("name") == self.tool_name
        ]
        target = _select(calls, self.occurrence)
        if target is None:
            return MutationApplication(
                applied=False,
                reason=f"No successful result for tool {self.tool_name!r}",
            )
        if target.payload.get("result") == self.replacement:
            return MutationApplication(
                applied=True,
                equivalent=True,
                reason="Replacement is equivalent to the recorded result",
            )
        target.payload["result"] = self.replacement
        return MutationApplication(
            applied=True,
            reason=f"Replaced the result of tool {self.tool_name!r}",
            report=baseline,
            target_event_ids=[target.event_id],
        )


class DuplicateToolCallOperator:
    kind = MutationKind.TOOL_CALL_DUPLICATE.value

    def __init__(
        self,
        tool_name: str,
        *,
        occurrence: int = 0,
        operator_id: Optional[str] = None,
    ):
        self.tool_name = tool_name
        self.occurrence = occurrence
        self.operator_id = operator_id or f"tool_call_duplicate:{tool_name}:{occurrence}"

    def apply(self, scenario, baseline):
        calls = [
            event
            for event in baseline.trace.events
            if event.event_type in {
                TraceEventType.TOOL_EXCHANGE,
                TraceEventType.TOOL_ERROR,
            }
            and event.payload.get("name") == self.tool_name
        ]
        target = _select(calls, self.occurrence)
        if target is None:
            return MutationApplication(
                applied=False,
                reason=f"No call to tool {self.tool_name!r} at requested occurrence",
            )
        duplicate = TraceEvent.model_validate(target.model_dump())
        duplicate.event_id = f"{target.event_id}:duplicate:{self.operator_id}"
        index = baseline.trace.events.index(target)
        baseline.trace.events.insert(index + 1, duplicate)
        _renumber(baseline.trace.events)
        return MutationApplication(
            applied=True,
            reason=f"Duplicated a call to tool {self.tool_name!r}",
            report=baseline,
            target_event_ids=[target.event_id, duplicate.event_id],
        )


class MutationRunner:
    """Runs a baseline once, mutates detached reports, and reevaluates oracles."""

    def __init__(
        self,
        scenario_runner: ScenarioRunner,
        *,
        oracle_evaluator: Optional[OracleEvaluator] = None,
    ):
        self._scenario_runner = scenario_runner
        self._oracles = oracle_evaluator or scenario_runner.oracle_evaluator

    def run(
        self,
        scenario: Scenario,
        operators: Sequence[MutationOperator],
    ) -> MutationCampaignReport:
        self._validate_operators(operators)
        baseline = self._scenario_runner.run(scenario)
        outcomes = []
        for operator in operators:
            if not baseline.passed:
                outcomes.append(
                    MutationOutcome(
                        operator_id=operator.operator_id,
                        kind=operator.kind,
                        status=MutationStatus.BASELINE_FAILED,
                        message="Baseline scenario failed; mutant was not evaluated",
                    )
                )
                continue
            detached = ScenarioReport.model_validate(baseline.model_dump())
            try:
                application = operator.apply(scenario, detached)
                outcomes.append(
                    self._classify(scenario, operator, application)
                )
            except Exception as exc:
                outcomes.append(
                    MutationOutcome(
                        operator_id=operator.operator_id,
                        kind=operator.kind,
                        status=MutationStatus.ERROR,
                        message=str(exc),
                        error_type=type(exc).__name__,
                    )
                )
        killed = sum(item.status is MutationStatus.KILLED for item in outcomes)
        survived = sum(item.status is MutationStatus.SURVIVED for item in outcomes)
        not_applicable = sum(
            item.status is MutationStatus.NOT_APPLICABLE for item in outcomes
        )
        equivalent = sum(item.status is MutationStatus.EQUIVALENT for item in outcomes)
        invalid = sum(item.status is MutationStatus.INVALID for item in outcomes)
        errors = sum(item.status is MutationStatus.ERROR for item in outcomes)
        denominator = killed + survived
        return MutationCampaignReport(
            scenario_id=scenario.scenario_id,
            baseline=baseline,
            outcomes=outcomes,
            killed=killed,
            survived=survived,
            not_applicable=not_applicable,
            equivalent=equivalent,
            invalid=invalid,
            errors=errors,
            mutation_score=killed / denominator if denominator else None,
        )

    def _classify(self, scenario, operator, application):
        if not application.applied:
            return MutationOutcome(
                operator_id=operator.operator_id,
                kind=operator.kind,
                status=MutationStatus.NOT_APPLICABLE,
                message=application.reason,
                target_event_ids=application.target_event_ids,
            )
        if not application.valid:
            return MutationOutcome(
                operator_id=operator.operator_id,
                kind=operator.kind,
                status=MutationStatus.INVALID,
                message=application.reason,
                target_event_ids=application.target_event_ids,
            )
        if application.equivalent:
            return MutationOutcome(
                operator_id=operator.operator_id,
                kind=operator.kind,
                status=MutationStatus.EQUIVALENT,
                message=application.reason,
                target_event_ids=application.target_event_ids,
            )
        if application.report is None:
            raise ValueError("Applied mutation did not return a scenario report")
        if application.report.scenario_id != scenario.scenario_id:
            raise ValueError("Mutation changed the scenario identity")
        report = application.report
        verdicts = self._oracles.evaluate(
            scenario,
            result=report.result,
            trace=report.trace,
            execution_error=report.execution_error,
        )
        report.verdicts = verdicts
        report.passed = all(
            verdict.passed or verdict.severity is OracleSeverity.WARNING
            for verdict in verdicts
        )
        failed_oracle_ids = [
            verdict.oracle_id for verdict in verdicts if not verdict.passed
        ]
        return MutationOutcome(
            operator_id=operator.operator_id,
            kind=operator.kind,
            status=(
                MutationStatus.SURVIVED if report.passed else MutationStatus.KILLED
            ),
            message=application.reason,
            target_event_ids=application.target_event_ids,
            failed_oracle_ids=failed_oracle_ids,
            verdicts=verdicts,
        )

    @staticmethod
    def _validate_operators(operators: Sequence[MutationOperator]) -> None:
        operator_ids = [operator.operator_id for operator in operators]
        duplicates = sorted(
            operator_id
            for operator_id in set(operator_ids)
            if operator_ids.count(operator_id) > 1
        )
        if duplicates:
            raise MutationConfigurationError(
                f"Duplicate mutation operator IDs: {duplicates}"
            )
