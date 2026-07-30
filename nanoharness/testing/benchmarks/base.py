"""Frozen provenance for converting external benchmark tasks into Scenarios."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from nanoharness.testing.scenario import Scenario
from nanoharness.testing.trace import normalize_trace_value


BENCHMARK_MANIFEST_SCHEMA_VERSION = 1


class BenchmarkSourceIdentity(BaseModel):
    """Immutable package and source revision for an external benchmark."""

    benchmark_id: str = Field(min_length=1)
    distribution: str = Field(min_length=1)
    package_version: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    license: str = Field(min_length=1)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_and_validate(self):
        if self.revision.casefold() in {"head", "latest", "main", "master"}:
            raise ValueError("Benchmark source revision must be immutable")
        self.metadata = normalize_trace_value(self.metadata)
        return self


class BenchmarkScorerProvenance(BaseModel):
    """Scorer identity retained without claiming it is a NanoHarness Oracle."""

    utility_callable: str = Field(min_length=1)
    trace_callable: str = Field(min_length=1)
    trace_aware: bool
    strict_default: Optional[bool] = None
    requires_pre_environment: bool = True
    requires_post_environment: bool = True


class BenchmarkScenarioRecord(BaseModel):
    """One source task and its loss-aware, offline Scenario conversion."""

    source_task_id: str = Field(min_length=1)
    task_kind: Literal["user"] = "user"
    difficulty: str = Field(min_length=1)
    scenario: Scenario
    pre_environment_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_tool_calls: List[Dict[str, Any]] = Field(default_factory=list)
    ground_truth_output: str = ""
    scorer: BenchmarkScorerProvenance

    @model_validator(mode="after")
    def normalize_reference_calls(self):
        self.reference_tool_calls = normalize_trace_value(
            self.reference_tool_calls
        )
        return self


class BenchmarkManifest(BaseModel):
    """Frozen selection and conversion of an external benchmark suite."""

    schema_version: int = Field(
        default=BENCHMARK_MANIFEST_SCHEMA_VERSION,
        ge=1,
    )
    manifest_id: str = Field(min_length=1)
    source: BenchmarkSourceIdentity
    benchmark_version: str = Field(min_length=1)
    suite: str = Field(min_length=1)
    tasks: List[BenchmarkScenarioRecord] = Field(min_length=1)
    conversion_scope: Literal["offline_task_to_scenario"] = (
        "offline_task_to_scenario"
    )
    oracle_binding: Literal["unbound"] = "unbound"
    frozen_at: datetime
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_manifest(self):
        if self.frozen_at.utcoffset() is None:
            raise ValueError("Benchmark manifest frozen_at requires a timezone")
        source_ids = [task.source_task_id for task in self.tasks]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("Benchmark source task IDs must be unique")
        scenario_ids = [task.scenario.scenario_id for task in self.tasks]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("Converted Scenario IDs must be unique")
        self.metadata = normalize_trace_value(self.metadata)
        expected = benchmark_manifest_digest(
            self.manifest_id,
            self.source,
            self.benchmark_version,
            self.suite,
            self.tasks,
            frozen_at=self.frozen_at,
            metadata=self.metadata,
        )
        if self.manifest_digest != expected:
            raise ValueError("Benchmark manifest_digest does not match contents")
        return self

    def assert_unchanged(self) -> None:
        expected = benchmark_manifest_digest(
            self.manifest_id,
            self.source,
            self.benchmark_version,
            self.suite,
            self.tasks,
            frozen_at=self.frozen_at,
            metadata=self.metadata,
        )
        if self.manifest_digest != expected:
            raise BenchmarkConfigurationError(
                f"Benchmark manifest {self.manifest_id!r} changed after freezing"
            )


class BenchmarkConfigurationError(ValueError):
    """Raised when a benchmark conversion cannot preserve its contract."""


def benchmark_manifest_digest(
    manifest_id: str,
    source: BenchmarkSourceIdentity,
    benchmark_version: str,
    suite: str,
    tasks: Sequence[BenchmarkScenarioRecord],
    *,
    frozen_at: datetime,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Return the stable digest bound by a benchmark conversion Manifest."""

    payload = {
        "schema_version": BENCHMARK_MANIFEST_SCHEMA_VERSION,
        "manifest_id": manifest_id,
        "source": source.model_dump(mode="json"),
        "benchmark_version": benchmark_version,
        "suite": suite,
        "tasks": [task.model_dump(mode="json") for task in tasks],
        "conversion_scope": "offline_task_to_scenario",
        "oracle_binding": "unbound",
        "frozen_at": frozen_at.isoformat(),
        "metadata": normalize_trace_value(metadata or {}),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def canonical_value_digest(value: Any) -> str:
    """Digest a JSON-normalized fixture without archiving the full fixture."""

    canonical = json.dumps(
        normalize_trace_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
