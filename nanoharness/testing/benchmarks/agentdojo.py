"""Loss-aware offline conversion of pinned AgentDojo user tasks."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from importlib import import_module, metadata
from typing import Any, Optional

from nanoharness.testing.benchmarks.base import (
    BenchmarkConfigurationError,
    BenchmarkManifest,
    BenchmarkScenarioRecord,
    BenchmarkScorerProvenance,
    BenchmarkSourceIdentity,
    benchmark_manifest_digest,
    canonical_value_digest,
)
from nanoharness.testing.scenario import Scenario
from nanoharness.testing.trace import normalize_trace_value


AGENTDOJO_DISTRIBUTION = "agentdojo"
AGENTDOJO_PACKAGE_VERSION = "0.1.35"
AGENTDOJO_REVISION = "a75aba7631d3ca5fb7ab938965c97ead2f9ff84b"
AGENTDOJO_SOURCE_URL = "https://github.com/ethz-spylab/agentdojo"
AGENTDOJO_BENCHMARK_VERSION = "v1.2.2"


SuiteLoader = Callable[[str, str], Any]


class AgentDojoUnavailableError(ImportError):
    """Raised when pinned AgentDojo package provenance is unavailable."""


class AgentDojoBenchmarkAdapter:
    """Convert AgentDojo user tasks without transplanting its stateful scorer.

    AgentDojo utility functions require both pre- and post-execution
    environments. The converted Scenario therefore has no NanoHarness Oracle.
    It retains scorer provenance and non-normative reference calls so a later
    runtime integration can bind the original scorer without silently changing
    its semantics.
    """

    def __init__(
        self,
        source: BenchmarkSourceIdentity,
        suite_loader: SuiteLoader,
    ):
        if source.distribution.casefold() != AGENTDOJO_DISTRIBUTION:
            raise ValueError("AgentDojo source distribution must be 'agentdojo'")
        self._source = BenchmarkSourceIdentity.model_validate(
            source.model_dump()
        )
        self._suite_loader = suite_loader

    @classmethod
    def from_installed(
        cls,
        *,
        expected_package_version: str = AGENTDOJO_PACKAGE_VERSION,
        revision: Optional[str] = None,
    ) -> "AgentDojoBenchmarkAdapter":
        try:
            installed_version = metadata.version(AGENTDOJO_DISTRIBUTION)
            module = import_module("agentdojo.task_suite.load_suites")
        except (metadata.PackageNotFoundError, ImportError) as exc:
            raise AgentDojoUnavailableError(
                "Install the pinned AgentDojo research dependency to convert "
                "benchmark tasks"
            ) from exc
        if installed_version != expected_package_version:
            raise BenchmarkConfigurationError(
                "Installed AgentDojo version "
                f"{installed_version!r} does not match pinned version "
                f"{expected_package_version!r}"
            )
        if revision is None:
            if installed_version != AGENTDOJO_PACKAGE_VERSION:
                raise BenchmarkConfigurationError(
                    "An explicit immutable revision is required for an "
                    "AgentDojo package version without a built-in provenance "
                    "mapping"
                )
            revision = AGENTDOJO_REVISION
        return cls(
            BenchmarkSourceIdentity(
                benchmark_id=f"agentdojo@pypi-{installed_version}",
                distribution=AGENTDOJO_DISTRIBUTION,
                package_version=installed_version,
                revision=revision,
                source_url=AGENTDOJO_SOURCE_URL,
                license="MIT",
                metadata={
                    "package_index": "PyPI",
                    "revision_kind": "git_commit",
                },
            ),
            module.get_suite,
        )

    @property
    def source(self) -> BenchmarkSourceIdentity:
        return BenchmarkSourceIdentity.model_validate(self._source.model_dump())

    def convert_user_tasks(
        self,
        suite: str,
        task_ids: Sequence[str],
        *,
        benchmark_version: str = AGENTDOJO_BENCHMARK_VERSION,
        manifest_id: Optional[str] = None,
        frozen_at: Optional[datetime] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> BenchmarkManifest:
        selected_ids = list(task_ids)
        if not selected_ids:
            raise BenchmarkConfigurationError(
                "Select at least one AgentDojo user task explicitly"
            )
        if len(selected_ids) != len(set(selected_ids)):
            raise BenchmarkConfigurationError(
                "AgentDojo task selection contains duplicate IDs"
            )
        try:
            task_suite = self._suite_loader(benchmark_version, suite)
        except KeyError as exc:
            raise BenchmarkConfigurationError(
                f"Unknown AgentDojo suite {suite!r} for {benchmark_version!r}"
            ) from exc
        available = task_suite.user_tasks
        unknown = [task_id for task_id in selected_ids if task_id not in available]
        if unknown:
            raise BenchmarkConfigurationError(
                f"Unknown AgentDojo user task IDs in {suite!r}: {unknown}"
            )

        records = [
            self._convert_task(task_suite, suite, benchmark_version, task_id)
            for task_id in selected_ids
        ]
        timestamp = frozen_at or datetime.now(timezone.utc)
        if timestamp.utcoffset() is None:
            raise BenchmarkConfigurationError("frozen_at requires a timezone")
        resolved_manifest_id = manifest_id or (
            f"agentdojo-{benchmark_version}-{suite}-offline"
        )
        resolved_metadata = normalize_trace_value(metadata or {})
        digest = benchmark_manifest_digest(
            resolved_manifest_id,
            self._source,
            benchmark_version,
            suite,
            records,
            frozen_at=timestamp,
            metadata=resolved_metadata,
        )
        return BenchmarkManifest(
            manifest_id=resolved_manifest_id,
            source=self._source,
            benchmark_version=benchmark_version,
            suite=suite,
            tasks=records,
            frozen_at=timestamp,
            manifest_digest=digest,
            metadata=resolved_metadata,
        )

    def _convert_task(
        self,
        task_suite: Any,
        suite: str,
        benchmark_version: str,
        task_id: str,
    ) -> BenchmarkScenarioRecord:
        task = task_suite.user_tasks[task_id]
        environment = task_suite.load_and_inject_default_environment({})
        initialized = task.init_environment(environment)
        pre_environment = initialized.model_copy(deep=True)
        calls = task.ground_truth(pre_environment.model_copy(deep=True))
        reference_calls = [
            normalize_trace_value(call.model_dump(mode="json")) for call in calls
        ]
        environment_value = pre_environment.model_dump(mode="json")
        environment_digest = canonical_value_digest(environment_value)
        difficulty_value = getattr(task, "DIFFICULTY", "unknown")
        difficulty = getattr(difficulty_value, "name", str(difficulty_value)).lower()
        utility = type(task).utility
        trace_utility = type(task).utility_from_traces
        trace_callable = _callable_name(trace_utility)
        trace_aware = not trace_callable.endswith(
            "BaseUserTask.utility_from_traces"
        )
        scorer = BenchmarkScorerProvenance(
            utility_callable=_callable_name(utility),
            trace_callable=trace_callable,
            trace_aware=trace_aware,
            strict_default=True,
        )
        source_metadata = {
            "benchmark_id": self._source.benchmark_id,
            "package_version": self._source.package_version,
            "revision": self._source.revision,
            "benchmark_version": benchmark_version,
            "suite": suite,
            "source_task_id": task_id,
            "task_kind": "user",
            "conversion_scope": "offline_task_to_scenario",
            "oracle_binding": "unbound",
            "scorer": scorer.model_dump(mode="json"),
        }
        scenario = Scenario(
            scenario_id=f"agentdojo-{benchmark_version}-{suite}-{task_id}",
            query=task.PROMPT,
            tags=[
                "benchmark:agentdojo",
                f"benchmark-version:{benchmark_version}",
                f"suite:{suite}",
                f"source-task:{task_id}",
            ],
            fixtures={
                "agentdojo_reference": {
                    "semantics": "non_normative_reference_plan",
                    "pre_environment_digest": environment_digest,
                    "tool_calls": reference_calls,
                    "ground_truth_output": getattr(
                        task,
                        "GROUND_TRUTH_OUTPUT",
                        "",
                    ),
                }
            },
            metadata={"benchmark": source_metadata},
            oracles=[],
        )
        return BenchmarkScenarioRecord(
            source_task_id=task_id,
            difficulty=difficulty,
            scenario=scenario,
            pre_environment_digest=environment_digest,
            reference_tool_calls=reference_calls,
            ground_truth_output=getattr(task, "GROUND_TRUTH_OUTPUT", ""),
            scorer=scorer,
        )


def _callable_name(value: Any) -> str:
    return f"{value.__module__}.{value.__qualname__}"
