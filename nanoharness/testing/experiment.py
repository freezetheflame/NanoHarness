"""Frozen manifests and deterministic raw-result collection for experiments."""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

from nanoharness.testing.adapters.base import SubjectAdapter, SubjectIdentity
from nanoharness.testing.scenario import Scenario, ScenarioReport
from nanoharness.testing.trace import normalize_trace_value


EXPERIMENT_SCHEMA_VERSION = 1


class ExperimentCell(BaseModel):
    """One subject/scenario pair with preregistered repetition seeds."""

    cell_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    scenario: Scenario
    seeds: List[int] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_seeds(self):
        if self.scenario.seed is not None:
            raise ValueError(
                "Experiment cell Scenario seed must be unset; "
                "cell seeds are authoritative"
            )
        return self


class ExperimentManifest(BaseModel):
    """Frozen subject provenance, scenarios, order, and repetition seeds."""

    schema_version: int = Field(default=EXPERIMENT_SCHEMA_VERSION, ge=1)
    experiment_id: str = Field(min_length=1)
    subjects: List[SubjectIdentity] = Field(min_length=1)
    cells: List[ExperimentCell] = Field(min_length=1)
    frozen_at: datetime
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_manifest(self):
        if self.frozen_at.utcoffset() is None:
            raise ValueError("Experiment frozen_at must include a timezone offset")
        subject_ids = [subject.subject_id for subject in self.subjects]
        if len(set(subject_ids)) != len(subject_ids):
            raise ValueError("Experiment subject IDs must be unique")
        cell_ids = [cell.cell_id for cell in self.cells]
        if len(set(cell_ids)) != len(cell_ids):
            raise ValueError("Experiment cell IDs must be unique")
        unknown = sorted(
            {cell.subject_id for cell in self.cells} - set(subject_ids)
        )
        if unknown:
            raise ValueError(f"Experiment cells reference unknown subjects: {unknown}")
        self.metadata = normalize_trace_value(self.metadata)
        expected = experiment_manifest_digest(
            self.experiment_id,
            self.subjects,
            self.cells,
            frozen_at=self.frozen_at,
            metadata=self.metadata,
        )
        if self.manifest_digest != expected:
            raise ValueError("Experiment manifest_digest does not match contents")
        return self

    def assert_unchanged(self) -> None:
        expected = experiment_manifest_digest(
            self.experiment_id,
            self.subjects,
            self.cells,
            frozen_at=self.frozen_at,
            metadata=self.metadata,
        )
        if self.manifest_digest != expected:
            raise ExperimentConfigurationError(
                f"Experiment manifest {self.experiment_id!r} changed after freezing"
            )


class ExperimentObservation(BaseModel):
    """One raw subject execution with its exact repetition identity."""

    observation_id: str
    cell_id: str
    subject: SubjectIdentity
    scenario_id: str
    repetition: int = Field(ge=0)
    seed: int
    duration_ms: float = Field(ge=0)
    report: ScenarioReport


class SubjectExperimentSummary(BaseModel):
    """Descriptive subject totals; raw observations remain authoritative."""

    subject_id: str
    runs: int = Field(ge=0)
    passed: int = Field(ge=0)
    execution_errors: int = Field(ge=0)
    pass_rate: Optional[float] = Field(default=None, ge=0, le=1)


class ExperimentReport(BaseModel):
    """Serializable raw observations and minimal descriptive summaries."""

    schema_version: int = Field(default=EXPERIMENT_SCHEMA_VERSION, ge=1)
    manifest: ExperimentManifest
    started_at: datetime
    finished_at: datetime
    observations: List[ExperimentObservation] = Field(default_factory=list)
    subjects: List[SubjectExperimentSummary] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_timestamps(self):
        if self.started_at.utcoffset() is None or self.finished_at.utcoffset() is None:
            raise ValueError(
                "Experiment report timestamps must include timezone offsets"
            )
        if self.finished_at < self.started_at:
            raise ValueError("Experiment finished_at cannot precede started_at")
        expected = []
        subjects = {
            subject.subject_id: subject for subject in self.manifest.subjects
        }
        for cell in self.manifest.cells:
            for repetition, seed in enumerate(cell.seeds):
                expected.append(
                    (
                        f"{cell.cell_id}:r{repetition}",
                        cell.cell_id,
                        subjects[cell.subject_id],
                        cell.scenario.scenario_id,
                        repetition,
                        seed,
                    )
                )
        if len(self.observations) != len(expected):
            raise ValueError(
                "Experiment observation count does not match frozen Manifest"
            )
        trace_ids = set()
        for observation, expected_fields in zip(self.observations, expected):
            actual_fields = (
                observation.observation_id,
                observation.cell_id,
                observation.subject,
                observation.scenario_id,
                observation.repetition,
                observation.seed,
            )
            if actual_fields != expected_fields:
                raise ValueError(
                    f"Observation {observation.observation_id!r} does not match "
                    "frozen Manifest order or provenance"
                )
            trace_id = observation.report.trace.trace_id
            if trace_id in trace_ids:
                raise ValueError(f"Duplicate Trace ID in report: {trace_id!r}")
            trace_ids.add(trace_id)
        expected_summaries = _summaries(
            self.observations,
            self.manifest.subjects,
        )
        if self.subjects != expected_summaries:
            raise ValueError("Experiment subject summaries do not match observations")
        return self


class ExperimentConfigurationError(ValueError):
    """Raised before execution when manifest and adapters disagree."""


class ExperimentRunner:
    """Run a frozen manifest serially and retain every raw Scenario report."""

    def __init__(
        self,
        adapters: Sequence[SubjectAdapter],
        *,
        clock: Callable[[], float] = time.perf_counter,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        identities = [adapter.identity for adapter in adapters]
        subject_ids = [identity.subject_id for identity in identities]
        duplicates = sorted(
            subject_id
            for subject_id in set(subject_ids)
            if subject_ids.count(subject_id) > 1
        )
        if duplicates:
            raise ExperimentConfigurationError(
                f"Duplicate adapter subject IDs: {duplicates}"
            )
        self._adapters = {
            adapter.identity.subject_id: adapter for adapter in adapters
        }
        self._clock = clock
        self._wall_clock = wall_clock

    def run(self, manifest: ExperimentManifest) -> ExperimentReport:
        if manifest.schema_version != EXPERIMENT_SCHEMA_VERSION:
            raise ExperimentConfigurationError(
                f"Unsupported experiment schema version {manifest.schema_version}"
            )
        manifest.assert_unchanged()
        self._validate_adapters(manifest)
        started_at = self._wall_clock()
        if started_at.utcoffset() is None:
            raise ExperimentConfigurationError(
                "Experiment wall clock must return timezone-aware datetimes"
            )
        observations = []
        trace_ids = set()
        for cell in manifest.cells:
            adapter = self._adapters[cell.subject_id]
            for repetition, seed in enumerate(cell.seeds):
                scenario = Scenario.model_validate(
                    {**cell.scenario.model_dump(), "seed": seed}
                )
                started = self._clock()
                report = adapter.run(scenario)
                duration_ms = (self._clock() - started) * 1000
                if duration_ms < 0:
                    raise ExperimentConfigurationError(
                        "Experiment monotonic clock moved backwards"
                    )
                self._validate_report(adapter, scenario, report)
                if report.trace.trace_id in trace_ids:
                    raise ExperimentConfigurationError(
                        f"Duplicate Trace ID {report.trace.trace_id!r} in experiment"
                    )
                trace_ids.add(report.trace.trace_id)
                observations.append(
                    ExperimentObservation(
                        observation_id=f"{cell.cell_id}:r{repetition}",
                        cell_id=cell.cell_id,
                        subject=adapter.identity,
                        scenario_id=scenario.scenario_id,
                        repetition=repetition,
                        seed=seed,
                        duration_ms=duration_ms,
                        report=report,
                    )
                )
        finished_at = self._wall_clock()
        if finished_at.utcoffset() is None or finished_at < started_at:
            raise ExperimentConfigurationError(
                "Experiment wall clock returned invalid finish time"
            )
        return ExperimentReport(
            manifest=ExperimentManifest.model_validate(manifest.model_dump()),
            started_at=started_at,
            finished_at=finished_at,
            observations=observations,
            subjects=_summaries(observations, manifest.subjects),
        )

    def _validate_adapters(self, manifest: ExperimentManifest) -> None:
        expected = {subject.subject_id: subject for subject in manifest.subjects}
        missing = sorted(set(expected) - set(self._adapters))
        extra = sorted(set(self._adapters) - set(expected))
        if missing or extra:
            raise ExperimentConfigurationError(
                f"Adapter/manifest mismatch; missing={missing}, extra={extra}"
            )
        for subject_id, identity in expected.items():
            actual = self._adapters[subject_id].identity
            if actual != identity:
                raise ExperimentConfigurationError(
                    f"Adapter identity mismatch for subject {subject_id!r}"
                )

    @staticmethod
    def _validate_report(
        adapter: SubjectAdapter,
        scenario: Scenario,
        report: ScenarioReport,
    ) -> None:
        if report.scenario_id != scenario.scenario_id:
            raise ExperimentConfigurationError(
                f"Adapter returned Scenario {report.scenario_id!r}; expected "
                f"{scenario.scenario_id!r}"
            )
        expected_subject = adapter.identity.model_dump(mode="json")
        if report.trace.metadata.get("subject") != expected_subject:
            raise ExperimentConfigurationError(
                f"Trace {report.trace.trace_id!r} lacks matching subject provenance"
            )
        if report.trace.metadata.get("seed") != scenario.seed:
            raise ExperimentConfigurationError(
                f"Trace {report.trace.trace_id!r} lacks matching seed provenance"
            )


def experiment_manifest_digest(
    experiment_id: str,
    subjects: Sequence[SubjectIdentity],
    cells: Sequence[ExperimentCell],
    *,
    frozen_at: datetime,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Return the stable digest bound by a frozen experiment manifest."""

    payload = {
        "schema_version": EXPERIMENT_SCHEMA_VERSION,
        "experiment_id": experiment_id,
        "frozen_at": frozen_at.isoformat(),
        "subjects": [subject.model_dump(mode="json") for subject in subjects],
        "cells": [cell.model_dump(mode="json") for cell in cells],
        "metadata": normalize_trace_value(metadata or {}),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _summaries(
    observations: Sequence[ExperimentObservation],
    subjects: Sequence[SubjectIdentity],
) -> List[SubjectExperimentSummary]:
    runs = Counter(item.subject.subject_id for item in observations)
    passed = Counter(
        item.subject.subject_id for item in observations if item.report.passed
    )
    errors = Counter(
        item.subject.subject_id
        for item in observations
        if item.report.execution_error is not None
    )
    return [
        SubjectExperimentSummary(
            subject_id=subject.subject_id,
            runs=runs[subject.subject_id],
            passed=passed[subject.subject_id],
            execution_errors=errors[subject.subject_id],
            pass_rate=(
                passed[subject.subject_id] / runs[subject.subject_id]
                if runs[subject.subject_id]
                else None
            ),
        )
        for subject in subjects
    ]
