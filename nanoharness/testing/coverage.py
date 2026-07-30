"""Explicit-universe behavioral coverage for agent scenario reports."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Union

from pydantic import BaseModel, Field, model_validator

from nanoharness.core.schema import RunStatus, StopReason
from nanoharness.testing.faults import FaultAction, FaultComponent, FaultReport
from nanoharness.testing.scenario import ScenarioReport
from nanoharness.testing.trace import (
    TRACE_SCHEMA_VERSION,
    TraceEvent,
    TraceEventType,
    normalize_trace_value,
    redact_sensitive_fields,
)


COVERAGE_SCHEMA_VERSION = 1


class CoverageKind(str, Enum):
    """Behavior dimensions observable through current stable contracts."""

    TOOL_CALL = "tool_call"
    TOOL_ARGUMENT = "tool_argument"
    RUN_STATUS = "run_status"
    STOP_REASON = "stop_reason"
    LIFECYCLE_EVENT = "lifecycle_event"
    COMPONENT_ERROR = "component_error"
    EXECUTION_ERROR = "execution_error"
    ORACLE_OUTCOME = "oracle_outcome"
    FAULT_ACTION = "fault_action"
    FAULT_OUTCOME = "fault_outcome"
    TRACE_METADATA = "trace_metadata"


class ArgumentMatcher(str, Enum):
    """Serializable equivalence-class matchers for tool arguments."""

    PRESENT = "present"
    MISSING = "missing"
    EQUALS = "equals"
    ONE_OF = "one_of"
    TYPE = "type"
    RANGE = "range"
    REGEX = "regex"


class CoverageModelStatus(str, Enum):
    """Lifecycle state separating examples, drafts, and frozen denominators."""

    TEMPLATE = "template"
    DRAFT = "draft"
    FROZEN = "frozen"


class CoverageTarget(BaseModel):
    """One preregistered behavior in the coverage denominator."""

    target_id: str = Field(min_length=1)
    kind: CoverageKind
    parameters: Dict[str, Any] = Field(default_factory=dict)
    description: str = ""
    required: bool = False
    tags: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_parameters(self):
        if len(set(self.tags)) != len(self.tags):
            raise ValueError("Coverage target tags must be unique")
        _validate_target_parameters(self.kind, self.parameters)
        self.parameters = normalize_trace_value(self.parameters)
        return self


class CoverageModel(BaseModel):
    """Versioned, explicit universe used as the coverage denominator."""

    schema_version: int = Field(default=COVERAGE_SCHEMA_VERSION, ge=1)
    model_id: str = Field(min_length=1)
    status: CoverageModelStatus = CoverageModelStatus.DRAFT
    subject_id: Optional[str] = None
    derivation: str = ""
    targets: List[CoverageTarget] = Field(default_factory=list)
    frozen_at: Optional[datetime] = None
    target_digest: Optional[str] = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_target_ids(self):
        target_ids = [target.target_id for target in self.targets]
        duplicates = sorted(
            target_id
            for target_id in set(target_ids)
            if target_ids.count(target_id) > 1
        )
        if duplicates:
            raise ValueError(f"Duplicate coverage target IDs: {duplicates}")
        if self.status is CoverageModelStatus.FROZEN:
            if not self.subject_id:
                raise ValueError("Frozen coverage models require subject_id")
            if not self.derivation:
                raise ValueError("Frozen coverage models require derivation")
            if self.frozen_at is None or self.frozen_at.utcoffset() is None:
                raise ValueError(
                    "Frozen coverage models require timezone-aware frozen_at"
                )
            expected_digest = coverage_target_digest(self.targets)
            if self.target_digest != expected_digest:
                raise ValueError(
                    "Frozen coverage model target_digest does not match targets"
                )
        elif self.frozen_at is not None or self.target_digest is not None:
            raise ValueError(
                "Only frozen coverage models may set frozen_at or target_digest"
            )
        self.metadata = normalize_trace_value(self.metadata)
        return self

    def assert_frozen(self) -> None:
        if self.status is not CoverageModelStatus.FROZEN:
            raise CoverageConfigurationError(
                f"Coverage model {self.model_id!r} is not frozen"
            )
        if self.target_digest != coverage_target_digest(self.targets):
            raise CoverageConfigurationError(
                f"Coverage model {self.model_id!r} changed after freezing"
            )


class CoverageEvidence(BaseModel):
    """A normalized fact showing why one target was covered."""

    scenario_id: str
    trace_id: str
    source: str
    event_id: Optional[str] = None
    sequence: Optional[int] = Field(default=None, ge=0)
    details: Dict[str, Any] = Field(default_factory=dict)


class CoverageTargetResult(BaseModel):
    """All evidence collected for one target."""

    target_id: str
    kind: CoverageKind
    required: bool
    covered: bool
    hit_count: int = Field(ge=0)
    scenario_ids: List[str] = Field(default_factory=list)
    trace_ids: List[str] = Field(default_factory=list)
    evidence: List[CoverageEvidence] = Field(default_factory=list)


class CoverageDimensionSummary(BaseModel):
    """Covered and total targets for one behavior dimension."""

    kind: CoverageKind
    covered: int = Field(ge=0)
    total: int = Field(ge=0)
    ratio: Optional[float] = Field(default=None, ge=0, le=1)


class CoverageReport(BaseModel):
    """Serializable aggregate over a fixed model and one or more runs."""

    schema_version: int = Field(default=COVERAGE_SCHEMA_VERSION, ge=1)
    model: CoverageModel
    scenario_ids: List[str] = Field(default_factory=list)
    trace_ids: List[str] = Field(default_factory=list)
    target_results: List[CoverageTargetResult] = Field(default_factory=list)
    dimensions: List[CoverageDimensionSummary] = Field(default_factory=list)
    covered_targets: int = Field(ge=0)
    total_targets: int = Field(ge=0)
    coverage_ratio: Optional[float] = Field(default=None, ge=0, le=1)
    uncovered_target_ids: List[str] = Field(default_factory=list)
    uncovered_required_target_ids: List[str] = Field(default_factory=list)

    def evaluate_gate(self, *, minimum_ratio: Optional[float] = None) -> None:
        """Raise only for an explicit gate; coverage has no default threshold."""

        failures = []
        if self.uncovered_required_target_ids:
            failures.append(
                "required targets uncovered: "
                f"{self.uncovered_required_target_ids}"
            )
        if minimum_ratio is not None:
            if not 0 <= minimum_ratio <= 1:
                raise ValueError("minimum_ratio must be between 0 and 1")
            if self.coverage_ratio is None or self.coverage_ratio < minimum_ratio:
                failures.append(
                    f"coverage ratio {self.coverage_ratio} below {minimum_ratio}"
                )
        if failures:
            raise CoverageGateError("; ".join(failures))


class CoverageGateError(AssertionError):
    """Raised when an explicitly configured behavioral coverage gate fails."""


class CoverageConfigurationError(ValueError):
    """Raised when model or execution evidence cannot be aggregated safely."""


class CoverageRun(BaseModel):
    """Scenario execution with optional executable-fault evidence."""

    scenario_report: ScenarioReport
    fault_report: Optional[FaultReport] = None

    @model_validator(mode="after")
    def validate_fault_provenance(self):
        if self.fault_report is None:
            return self
        trace_plan_id = self.scenario_report.trace.metadata.get("fault_plan_id")
        if trace_plan_id is not None and trace_plan_id != self.fault_report.plan_id:
            raise ValueError(
                f"Fault report plan {self.fault_report.plan_id!r} does not match "
                f"Trace plan {trace_plan_id!r}"
            )
        return self


class CoverageCollector:
    """Matches reports against a preregistered behavioral target universe."""

    def collect(
        self,
        model: CoverageModel,
        runs: Sequence[Union[ScenarioReport, CoverageRun]],
        *,
        require_frozen: bool = False,
    ) -> CoverageReport:
        if model.schema_version != COVERAGE_SCHEMA_VERSION:
            raise ValueError(
                f"Coverage schema version {model.schema_version} is unsupported; "
                f"expected {COVERAGE_SCHEMA_VERSION}"
            )
        if require_frozen:
            model.assert_frozen()
        normalized_runs = [
            run if isinstance(run, CoverageRun) else CoverageRun(scenario_report=run)
            for run in runs
        ]
        trace_ids = [run.scenario_report.trace.trace_id for run in normalized_runs]
        duplicates = sorted(
            trace_id
            for trace_id in set(trace_ids)
            if trace_ids.count(trace_id) > 1
        )
        if duplicates:
            raise CoverageConfigurationError(
                f"Duplicate Trace IDs in coverage input: {duplicates}"
            )
        for run in normalized_runs:
            _validate_trace(run.scenario_report)
        results = []
        for target in model.targets:
            evidence = []
            for run in normalized_runs:
                evidence.extend(self._match(target, run))
            scenario_ids = _ordered_unique(item.scenario_id for item in evidence)
            trace_ids = _ordered_unique(item.trace_id for item in evidence)
            results.append(
                CoverageTargetResult(
                    target_id=target.target_id,
                    kind=target.kind,
                    required=target.required,
                    covered=bool(evidence),
                    hit_count=len(evidence),
                    scenario_ids=scenario_ids,
                    trace_ids=trace_ids,
                    evidence=evidence,
                )
            )
        covered = sum(result.covered for result in results)
        total = len(results)
        dimension_totals = Counter(result.kind for result in results)
        dimension_covered = Counter(
            result.kind for result in results if result.covered
        )
        dimensions = [
            CoverageDimensionSummary(
                kind=kind,
                covered=dimension_covered[kind],
                total=dimension_totals[kind],
                ratio=dimension_covered[kind] / dimension_totals[kind],
            )
            for kind in CoverageKind
            if dimension_totals[kind]
        ]
        return CoverageReport(
            model=CoverageModel.model_validate(model.model_dump()),
            scenario_ids=_ordered_unique(
                run.scenario_report.scenario_id for run in normalized_runs
            ),
            trace_ids=_ordered_unique(
                run.scenario_report.trace.trace_id for run in normalized_runs
            ),
            target_results=results,
            dimensions=dimensions,
            covered_targets=covered,
            total_targets=total,
            coverage_ratio=covered / total if total else None,
            uncovered_target_ids=[
                result.target_id for result in results if not result.covered
            ],
            uncovered_required_target_ids=[
                result.target_id
                for result in results
                if result.required and not result.covered
            ],
        )

    def _match(
        self,
        target: CoverageTarget,
        run: CoverageRun,
    ) -> List[CoverageEvidence]:
        report = run.scenario_report
        parameters = target.parameters
        if target.kind is CoverageKind.TOOL_CALL:
            return self._events(
                report,
                (
                    event
                    for event in report.trace.events
                    if event.event_type
                    in {TraceEventType.TOOL_EXCHANGE, TraceEventType.TOOL_ERROR}
                    and event.payload.get("name") == parameters["tool_name"]
                ),
                details={"tool_name": parameters["tool_name"]},
            )
        if target.kind is CoverageKind.TOOL_ARGUMENT:
            matches = []
            for event in report.trace.events:
                if event.event_type not in {
                    TraceEventType.TOOL_EXCHANGE,
                    TraceEventType.TOOL_ERROR,
                }:
                    continue
                if event.payload.get("name") != parameters["tool_name"]:
                    continue
                arguments = event.payload.get("arguments") or {}
                if _argument_matches(arguments, parameters):
                    matches.append(event)
            return self._events(
                report,
                matches,
                details={
                    "tool_name": parameters["tool_name"],
                    "argument": parameters["argument"],
                    "matcher": parameters["matcher"],
                },
            )
        if target.kind is CoverageKind.RUN_STATUS:
            matched = (
                report.result is not None
                and report.result.status.value == parameters["status"]
            )
            return (
                self._report_evidence(report, "run_result", parameters)
                if matched
                else []
            )
        if target.kind is CoverageKind.STOP_REASON:
            matched = (
                report.result is not None
                and report.result.stop_reason.value == parameters["reason"]
            )
            return (
                self._report_evidence(report, "run_result", parameters)
                if matched
                else []
            )
        if target.kind is CoverageKind.LIFECYCLE_EVENT:
            return self._events(
                report,
                (
                    event
                    for event in report.trace.events
                    if event.event_type.value == parameters["event_type"]
                ),
                details=parameters,
            )
        if target.kind is CoverageKind.COMPONENT_ERROR:
            event_types = {
                "model": TraceEventType.MODEL_ERROR,
                "tool": TraceEventType.TOOL_ERROR,
                "hook": TraceEventType.HOOK_FAILED,
            }
            return self._events(
                report,
                (
                    event
                    for event in report.trace.events
                    if event.event_type is event_types[parameters["component"]]
                ),
                details=parameters,
            )
        if target.kind is CoverageKind.EXECUTION_ERROR:
            error = report.execution_error
            matched = error is not None
            if matched and "error_type" in parameters:
                matched = error.error_type == parameters["error_type"]
            return (
                self._report_evidence(report, "execution_error", parameters)
                if matched
                else []
            )
        if target.kind is CoverageKind.ORACLE_OUTCOME:
            evidence = []
            for verdict in report.verdicts:
                if "oracle_id" in parameters:
                    matches_identity = verdict.oracle_id == parameters["oracle_id"]
                else:
                    matches_identity = verdict.kind == parameters["oracle_kind"]
                if matches_identity and verdict.passed is parameters["passed"]:
                    evidence.extend(
                        self._report_evidence(
                            report,
                            "oracle",
                            {
                                "oracle_id": verdict.oracle_id,
                                "oracle_kind": verdict.kind,
                                "passed": verdict.passed,
                            },
                        )
                    )
            return evidence
        if target.kind in {CoverageKind.FAULT_ACTION, CoverageKind.FAULT_OUTCOME}:
            return self._fault_evidence(target, run)
        if target.kind is CoverageKind.TRACE_METADATA:
            actual = report.trace.metadata.get(parameters["key"])
            matched = actual == parameters["value"]
            return (
                self._report_evidence(report, "trace_metadata", parameters)
                if matched
                else []
            )
        raise ValueError(f"Unsupported coverage kind: {target.kind.value}")

    @staticmethod
    def _events(
        report: ScenarioReport,
        events: Iterable[TraceEvent],
        *,
        details: Dict[str, Any],
    ) -> List[CoverageEvidence]:
        return [
            CoverageEvidence(
                scenario_id=report.scenario_id,
                trace_id=report.trace.trace_id,
                source="trace",
                event_id=event.event_id,
                sequence=event.sequence,
                details=_safe_details(details),
            )
            for event in events
        ]

    @staticmethod
    def _report_evidence(
        report: ScenarioReport,
        source: str,
        details: Dict[str, Any],
    ) -> List[CoverageEvidence]:
        return [
            CoverageEvidence(
                scenario_id=report.scenario_id,
                trace_id=report.trace.trace_id,
                source=source,
                details=_safe_details(details),
            )
        ]

    def _fault_evidence(
        self,
        target: CoverageTarget,
        run: CoverageRun,
    ) -> List[CoverageEvidence]:
        if run.fault_report is None:
            return []
        evidence = []
        for application in run.fault_report.applications:
            parameters = target.parameters
            if "rule_id" in parameters and application.rule_id != parameters["rule_id"]:
                continue
            if (
                "action" in parameters
                and application.action.value != parameters["action"]
            ):
                continue
            if (
                "component" in parameters
                and application.component.value != parameters["component"]
            ):
                continue
            if (
                "effective" in parameters
                and application.effective is not parameters["effective"]
            ):
                continue
            if (
                target.kind is CoverageKind.FAULT_OUTCOME
                and run.scenario_report.passed is not parameters["scenario_passed"]
            ):
                continue
            evidence.append(
                CoverageEvidence(
                    scenario_id=run.scenario_report.scenario_id,
                    trace_id=run.scenario_report.trace.trace_id,
                    source="fault",
                    sequence=application.sequence,
                    details=_safe_details(
                        {
                            "rule_id": application.rule_id,
                            "component": application.component.value,
                            "action": application.action.value,
                            "effective": application.effective,
                            "scenario_passed": run.scenario_report.passed,
                        }
                    ),
                )
            )
        return evidence


def _validate_target_parameters(kind: CoverageKind, parameters: Dict[str, Any]) -> None:
    required_by_kind = {
        CoverageKind.TOOL_CALL: {"tool_name"},
        CoverageKind.TOOL_ARGUMENT: {"tool_name", "argument", "matcher"},
        CoverageKind.RUN_STATUS: {"status"},
        CoverageKind.STOP_REASON: {"reason"},
        CoverageKind.LIFECYCLE_EVENT: {"event_type"},
        CoverageKind.COMPONENT_ERROR: {"component"},
        CoverageKind.EXECUTION_ERROR: set(),
        CoverageKind.ORACLE_OUTCOME: {"passed"},
        CoverageKind.FAULT_ACTION: set(),
        CoverageKind.FAULT_OUTCOME: {"scenario_passed"},
        CoverageKind.TRACE_METADATA: {"key", "value"},
    }
    missing = required_by_kind[kind] - set(parameters)
    if missing:
        raise ValueError(f"{kind.value} target misses parameters: {sorted(missing)}")
    allowed_by_kind = {
        CoverageKind.TOOL_CALL: {"tool_name"},
        CoverageKind.TOOL_ARGUMENT: {
            "tool_name",
            "argument",
            "matcher",
            "value",
            "values",
            "value_type",
            "minimum",
            "maximum",
            "pattern",
        },
        CoverageKind.RUN_STATUS: {"status"},
        CoverageKind.STOP_REASON: {"reason"},
        CoverageKind.LIFECYCLE_EVENT: {"event_type"},
        CoverageKind.COMPONENT_ERROR: {"component"},
        CoverageKind.EXECUTION_ERROR: {"error_type"},
        CoverageKind.ORACLE_OUTCOME: {"oracle_id", "oracle_kind", "passed"},
        CoverageKind.FAULT_ACTION: {
            "rule_id",
            "component",
            "action",
            "effective",
        },
        CoverageKind.FAULT_OUTCOME: {
            "rule_id",
            "component",
            "action",
            "effective",
            "scenario_passed",
        },
        CoverageKind.TRACE_METADATA: {"key", "value"},
    }
    unexpected = set(parameters) - allowed_by_kind[kind]
    if unexpected:
        raise ValueError(
            f"{kind.value} target has unknown parameters: {sorted(unexpected)}"
        )
    if kind is CoverageKind.TOOL_ARGUMENT:
        _validate_argument_parameters(parameters)
    if kind in {CoverageKind.TOOL_CALL, CoverageKind.TOOL_ARGUMENT}:
        _require_nonempty_string(parameters, "tool_name", kind)
    if kind is CoverageKind.TOOL_ARGUMENT:
        _require_nonempty_string(parameters, "argument", kind)
    if kind is CoverageKind.RUN_STATUS:
        RunStatus(parameters["status"])
    if kind is CoverageKind.STOP_REASON:
        StopReason(parameters["reason"])
    if kind is CoverageKind.LIFECYCLE_EVENT:
        TraceEventType(parameters["event_type"])
    if kind is CoverageKind.COMPONENT_ERROR:
        if parameters["component"] not in {"model", "tool", "hook"}:
            raise ValueError("component_error component must be model, tool, or hook")
    if kind is CoverageKind.EXECUTION_ERROR and "error_type" in parameters:
        _require_nonempty_string(parameters, "error_type", kind)
    if kind is CoverageKind.ORACLE_OUTCOME:
        if ("oracle_id" in parameters) == ("oracle_kind" in parameters):
            raise ValueError(
                "oracle_outcome requires exactly one of oracle_id or oracle_kind"
            )
        if not isinstance(parameters["passed"], bool):
            raise ValueError("oracle_outcome passed must be boolean")
        identity = "oracle_id" if "oracle_id" in parameters else "oracle_kind"
        _require_nonempty_string(parameters, identity, kind)
    if kind in {CoverageKind.FAULT_ACTION, CoverageKind.FAULT_OUTCOME}:
        if "rule_id" not in parameters and "action" not in parameters:
            raise ValueError(f"{kind.value} requires rule_id or action")
        if "effective" in parameters and not isinstance(parameters["effective"], bool):
            raise ValueError(f"{kind.value} effective must be boolean")
        if "action" in parameters:
            FaultAction(parameters["action"])
        if "component" in parameters:
            FaultComponent(parameters["component"])
        if "rule_id" in parameters:
            _require_nonempty_string(parameters, "rule_id", kind)
    if kind is CoverageKind.FAULT_OUTCOME and not isinstance(
        parameters["scenario_passed"], bool
    ):
        raise ValueError("fault_outcome scenario_passed must be boolean")
    if kind is CoverageKind.TRACE_METADATA:
        _require_nonempty_string(parameters, "key", kind)


def _validate_argument_parameters(parameters: Dict[str, Any]) -> None:
    try:
        matcher = ArgumentMatcher(parameters["matcher"])
    except ValueError as exc:
        raise ValueError(
            f"Unknown tool-argument matcher: {parameters['matcher']}"
        ) from exc
    matcher_fields = {
        ArgumentMatcher.PRESENT: set(),
        ArgumentMatcher.MISSING: set(),
        ArgumentMatcher.EQUALS: {"value"},
        ArgumentMatcher.ONE_OF: {"values"},
        ArgumentMatcher.TYPE: {"value_type"},
        ArgumentMatcher.RANGE: {"minimum", "maximum"},
        ArgumentMatcher.REGEX: {"pattern"},
    }
    unrelated = (
        set(parameters)
        - {"tool_name", "argument", "matcher"}
        - matcher_fields[matcher]
    )
    if unrelated:
        raise ValueError(
            f"{matcher.value} matcher has unrelated parameters: {sorted(unrelated)}"
        )
    if matcher is ArgumentMatcher.EQUALS and "value" not in parameters:
        raise ValueError("equals matcher requires value")
    if matcher is ArgumentMatcher.ONE_OF:
        if not isinstance(parameters.get("values"), list) or not parameters["values"]:
            raise ValueError("one_of matcher requires a non-empty values list")
    if matcher is ArgumentMatcher.TYPE:
        allowed = {"null", "boolean", "integer", "number", "string", "array", "object"}
        if parameters.get("value_type") not in allowed:
            raise ValueError(
                f"type matcher value_type must be one of {sorted(allowed)}"
            )
    if matcher is ArgumentMatcher.RANGE:
        if "minimum" not in parameters and "maximum" not in parameters:
            raise ValueError("range matcher requires minimum or maximum")
        minimum = parameters.get("minimum")
        maximum = parameters.get("maximum")
        for key, value in (("minimum", minimum), ("maximum", maximum)):
            if key in parameters and (
                isinstance(value, bool) or not isinstance(value, (int, float))
            ):
                raise ValueError("range matcher bounds must be numeric")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError("range matcher minimum cannot exceed maximum")
    if matcher is ArgumentMatcher.REGEX:
        if not isinstance(parameters.get("pattern"), str):
            raise ValueError("regex matcher requires pattern")
        try:
            re.compile(parameters["pattern"])
        except re.error as exc:
            raise ValueError(f"Invalid regex matcher pattern: {exc}") from exc


def _argument_matches(arguments: Dict[str, Any], parameters: Dict[str, Any]) -> bool:
    argument = parameters["argument"]
    matcher = ArgumentMatcher(parameters["matcher"])
    present = argument in arguments
    if matcher is ArgumentMatcher.PRESENT:
        return present
    if matcher is ArgumentMatcher.MISSING:
        return not present
    if not present:
        return False
    value = arguments[argument]
    if matcher is ArgumentMatcher.EQUALS:
        return value == parameters["value"]
    if matcher is ArgumentMatcher.ONE_OF:
        return value in parameters["values"]
    if matcher is ArgumentMatcher.TYPE:
        return _matches_json_type(value, parameters["value_type"])
    if matcher is ArgumentMatcher.RANGE:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        if "minimum" in parameters and value < parameters["minimum"]:
            return False
        if "maximum" in parameters and value > parameters["maximum"]:
            return False
        return True
    if matcher is ArgumentMatcher.REGEX:
        return (
            isinstance(value, str)
            and re.search(parameters["pattern"], value) is not None
        )
    raise ValueError(f"Unsupported tool-argument matcher: {matcher.value}")


def _matches_json_type(value: Any, value_type: str) -> bool:
    if value_type == "null":
        return value is None
    if value_type == "boolean":
        return isinstance(value, bool)
    if value_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if value_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if value_type == "string":
        return isinstance(value, str)
    if value_type == "array":
        return isinstance(value, list)
    if value_type == "object":
        return isinstance(value, dict)
    return False


def _ordered_unique(values: Iterable[str]) -> List[str]:
    return list(dict.fromkeys(values))


def _safe_details(details: Dict[str, Any]) -> Dict[str, Any]:
    return redact_sensitive_fields(normalize_trace_value(details))


def coverage_target_digest(targets: Sequence[CoverageTarget]) -> str:
    """Return a stable SHA-256 digest over the ordered target universe."""

    payload = [target.model_dump(mode="json") for target in targets]
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _require_nonempty_string(
    parameters: Dict[str, Any],
    key: str,
    kind: CoverageKind,
) -> None:
    if not isinstance(parameters[key], str) or not parameters[key]:
        raise ValueError(f"{kind.value} {key} must be a non-empty string")


def _validate_trace(report: ScenarioReport) -> None:
    trace = report.trace
    if trace.schema_version != TRACE_SCHEMA_VERSION:
        raise CoverageConfigurationError(
            f"Trace {trace.trace_id!r} uses unsupported schema version "
            f"{trace.schema_version}"
        )
    event_ids = set()
    previous_sequence = -1
    for event in trace.events:
        if event.schema_version != TRACE_SCHEMA_VERSION:
            raise CoverageConfigurationError(
                f"Event {event.event_id!r} uses unsupported schema version "
                f"{event.schema_version}"
            )
        if event.trace_id != trace.trace_id:
            raise CoverageConfigurationError(
                f"Event {event.event_id!r} belongs to another Trace"
            )
        if event.event_id in event_ids:
            raise CoverageConfigurationError(
                f"Duplicate event ID in Trace {trace.trace_id!r}: {event.event_id!r}"
            )
        if event.sequence <= previous_sequence:
            raise CoverageConfigurationError(
                f"Trace {trace.trace_id!r} sequences are not strictly increasing"
            )
        event_ids.add(event.event_id)
        previous_sequence = event.sequence
