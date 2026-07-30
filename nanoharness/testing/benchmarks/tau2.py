"""Loss-aware offline conversion of pinned tau2 benchmark tasks."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
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


TAU2_DISTRIBUTION = "tau2"
TAU2_PACKAGE_VERSION = "1.0.1"
TAU2_BENCHMARK_VERSION = "v1.0.1"
TAU2_REVISION = "fc0055dc4e0a316c3f83133267fbd6faaa770992"
TAU2_TAG_OBJECT = "b711c1ead46f55111bf765cf44d5da8bacc2d28c"
TAU2_SOURCE_URL = "https://github.com/sierra-research/tau2-bench"
TAU2_RETAIL_TASKS_SHA256 = (
    "8e03ebce7901bd6218e7a7dc3105faa9324091a68058f7fe61c65262868812e8"
)


class Tau2BenchmarkAdapter:
    """Convert raw tau2 JSON to unbound Scenarios without importing tau2.

    A tau2 task is an interactive user-simulation specification, not a single
    prompt. Conversion therefore retains the complete user scenario and leaves
    execution and native reward binding explicit. Reference actions are stored
    as a non-normative plan: retail reward does not require that exact call
    sequence, and equivalent end states must remain valid.
    """

    def __init__(
        self,
        source: BenchmarkSourceIdentity,
        tasks: Sequence[Mapping[str, Any]],
        *,
        tasks_sha256: str,
        domain_db: Optional[Mapping[str, Any]] = None,
        domain_db_sha256: Optional[str] = None,
        policy: Optional[str] = None,
        policy_sha256: Optional[str] = None,
        splits: Optional[Mapping[str, Sequence[str]]] = None,
        splits_sha256: Optional[str] = None,
    ):
        if source.distribution.casefold() != TAU2_DISTRIBUTION:
            raise ValueError("tau2 source distribution must be 'tau2'")
        self._source = BenchmarkSourceIdentity.model_validate(source.model_dump())
        self._tasks_sha256 = _validate_sha256(tasks_sha256, "tasks_sha256")
        self._domain_db = normalize_trace_value(domain_db or {})
        self._domain_db_sha256 = _optional_sha256(
            domain_db_sha256, "domain_db_sha256"
        )
        self._policy = policy
        self._policy_sha256 = _optional_sha256(policy_sha256, "policy_sha256")
        self._splits_sha256 = _optional_sha256(splits_sha256, "splits_sha256")
        self._splits = {
            str(name): [str(task_id) for task_id in task_ids]
            for name, task_ids in (splits or {}).items()
        }
        normalized_tasks = [normalize_trace_value(dict(task)) for task in tasks]
        task_ids = [str(task.get("id", "")) for task in normalized_tasks]
        if any(not task_id for task_id in task_ids):
            raise BenchmarkConfigurationError("Every tau2 task requires an ID")
        if len(task_ids) != len(set(task_ids)):
            raise BenchmarkConfigurationError("tau2 source contains duplicate task IDs")
        self._tasks = dict(zip(task_ids, normalized_tasks))

    @classmethod
    def from_files(
        cls,
        tasks_path: str | Path,
        *,
        expected_tasks_sha256: str = TAU2_RETAIL_TASKS_SHA256,
        domain_db_path: Optional[str | Path] = None,
        policy_path: Optional[str | Path] = None,
        splits_path: Optional[str | Path] = None,
        source: Optional[BenchmarkSourceIdentity] = None,
    ) -> "Tau2BenchmarkAdapter":
        """Load exact source bytes and reject a task-file checksum mismatch."""

        tasks_bytes = Path(tasks_path).read_bytes()
        actual_tasks_sha256 = hashlib.sha256(tasks_bytes).hexdigest()
        if actual_tasks_sha256 != expected_tasks_sha256:
            raise BenchmarkConfigurationError(
                "tau2 tasks checksum mismatch: "
                f"expected {expected_tasks_sha256}, got {actual_tasks_sha256}"
            )
        tasks = _decode_json(tasks_bytes, "tasks")
        if not isinstance(tasks, list):
            raise BenchmarkConfigurationError("tau2 tasks JSON must be a list")

        domain_db, domain_db_sha256 = _read_optional_json(domain_db_path, "DB")
        if domain_db_path is not None and not isinstance(domain_db, Mapping):
            raise BenchmarkConfigurationError("tau2 DB JSON must be an object")
        splits, splits_sha256 = _read_optional_json(splits_path, "splits")
        if splits_path is not None and not isinstance(splits, Mapping):
            raise BenchmarkConfigurationError(
                "tau2 splits JSON must be an object"
            )
        policy = None
        policy_sha256 = None
        if policy_path is not None:
            policy_bytes = Path(policy_path).read_bytes()
            try:
                policy = policy_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise BenchmarkConfigurationError(
                    "tau2 policy must be UTF-8 text"
                ) from exc
            policy_sha256 = hashlib.sha256(policy_bytes).hexdigest()

        resolved_source = source or BenchmarkSourceIdentity(
            benchmark_id=f"tau2-bench@{TAU2_BENCHMARK_VERSION}",
            distribution=TAU2_DISTRIBUTION,
            package_version=TAU2_PACKAGE_VERSION,
            revision=TAU2_REVISION,
            source_url=TAU2_SOURCE_URL,
            license="MIT",
            metadata={
                "tag": TAU2_BENCHMARK_VERSION,
                "tag_object": TAU2_TAG_OBJECT,
                "revision_kind": "git_commit",
                "python_requires": ">=3.12,<3.14",
            },
        )
        return cls(
            resolved_source,
            tasks,
            tasks_sha256=actual_tasks_sha256,
            domain_db=domain_db if isinstance(domain_db, Mapping) else None,
            domain_db_sha256=domain_db_sha256,
            policy=policy,
            policy_sha256=policy_sha256,
            splits=splits if isinstance(splits, Mapping) else None,
            splits_sha256=splits_sha256,
        )

    @property
    def source(self) -> BenchmarkSourceIdentity:
        return BenchmarkSourceIdentity.model_validate(self._source.model_dump())

    def convert_tasks(
        self,
        domain: str,
        task_ids: Sequence[str],
        *,
        benchmark_version: str = TAU2_BENCHMARK_VERSION,
        manifest_id: Optional[str] = None,
        frozen_at: Optional[datetime] = None,
        selection_provenance: Optional[Mapping[str, Any]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> BenchmarkManifest:
        selected_ids = [str(task_id) for task_id in task_ids]
        if not selected_ids:
            raise BenchmarkConfigurationError("Select at least one tau2 task")
        if len(selected_ids) != len(set(selected_ids)):
            raise BenchmarkConfigurationError(
                "tau2 task selection contains duplicate IDs"
            )
        unknown = [task_id for task_id in selected_ids if task_id not in self._tasks]
        if unknown:
            raise BenchmarkConfigurationError(
                f"Unknown tau2 task IDs in {domain!r}: {unknown}"
            )

        provenance = normalize_trace_value(dict(selection_provenance or {}))
        records = [
            self._convert_task(
                domain,
                benchmark_version,
                task_id,
                provenance,
            )
            for task_id in selected_ids
        ]
        timestamp = frozen_at or datetime.now(timezone.utc)
        if timestamp.utcoffset() is None:
            raise BenchmarkConfigurationError("frozen_at requires a timezone")
        resolved_manifest_id = manifest_id or (
            f"tau2-{benchmark_version}-{domain}-offline"
        )
        resolved_metadata = normalize_trace_value(
            {
                **dict(metadata or {}),
                "tasks_sha256": self._tasks_sha256,
                "domain_db_sha256": self._domain_db_sha256,
                "policy_sha256": self._policy_sha256,
                "splits_sha256": self._splits_sha256,
                "selection_provenance": provenance,
                "reference_action_semantics": "non_normative_reference_plan",
                "reward_views": ["T0-DB", "T0-native-full"],
            }
        )
        digest = benchmark_manifest_digest(
            resolved_manifest_id,
            self._source,
            benchmark_version,
            domain,
            records,
            frozen_at=timestamp,
            metadata=resolved_metadata,
        )
        return BenchmarkManifest(
            manifest_id=resolved_manifest_id,
            source=self._source,
            benchmark_version=benchmark_version,
            suite=domain,
            tasks=records,
            frozen_at=timestamp,
            manifest_digest=digest,
            metadata=resolved_metadata,
        )

    def _convert_task(
        self,
        domain: str,
        benchmark_version: str,
        task_id: str,
        selection_provenance: Mapping[str, Any],
    ) -> BenchmarkScenarioRecord:
        task = self._tasks[task_id]
        instructions = task.get("user_scenario", {}).get("instructions", {})
        task_domain = instructions.get("domain")
        if task_domain and task_domain != domain:
            raise BenchmarkConfigurationError(
                f"tau2 task {task_id!r} belongs to {task_domain!r}, not {domain!r}"
            )
        criteria = task.get("evaluation_criteria") or {}
        reference_actions = normalize_trace_value(criteria.get("actions") or [])
        reward_basis = [str(value) for value in criteria.get("reward_basis") or []]
        nl_assertions = normalize_trace_value(criteria.get("nl_assertions") or [])
        issues = normalize_trace_value(task.get("issues") or [])
        unresolved_issues = [
            issue
            for issue in issues
            if str(issue.get("status", "")).casefold()
            not in {"closed", "resolved"}
        ]
        split_membership = sorted(
            name for name, ids in self._splits.items() if task_id in ids
        )
        initial_fixture = {
            "domain_db": self._domain_db,
            "task_initial_state": task.get("initial_state"),
        }
        pre_environment_digest = canonical_value_digest(initial_fixture)
        requires_llm_judge = "NL_ASSERTION" in reward_basis and bool(nl_assertions)
        scorer = BenchmarkScorerProvenance(
            utility_callable="tau2.evaluator.evaluator.evaluate_simulation",
            trace_callable="tau2.evaluator.evaluator_env.EnvironmentEvaluator.calculate_reward",
            trace_aware=True,
            strict_default=True,
        )
        source_metadata = {
            "benchmark_id": self._source.benchmark_id,
            "package_version": self._source.package_version,
            "revision": self._source.revision,
            "benchmark_version": benchmark_version,
            "domain": domain,
            "source_task_id": task_id,
            "interaction_mode": "simulated_user",
            "conversion_scope": "offline_task_to_scenario",
            "oracle_binding": "unbound",
            "split_membership": split_membership,
            "issue_status": "unresolved" if unresolved_issues else "clear",
            "selection_provenance": selection_provenance,
        }
        scenario = Scenario(
            scenario_id=f"tau2-{benchmark_version}-{domain}-{task_id}",
            query=str(instructions.get("reason_for_call", "")),
            tags=[
                "benchmark:tau2",
                f"benchmark-version:{benchmark_version}",
                f"domain:{domain}",
                f"source-task:{task_id}",
            ],
            fixtures={
                "tau2_task": {
                    "user_scenario": task.get("user_scenario"),
                    "initial_state": task.get("initial_state"),
                    "description": task.get("description"),
                    "issues": issues,
                    "unresolved_issue_ids": [
                        str(issue.get("id", "")) for issue in unresolved_issues
                    ],
                    "split_membership": split_membership,
                },
                "tau2_reference": {
                    "semantics": "non_normative_reference_plan",
                    "actions": reference_actions,
                },
                "tau2_reward": {
                    "reward_basis": reward_basis,
                    "nl_assertions": nl_assertions,
                    "requires_llm_judge": requires_llm_judge,
                    "deterministic_view": "T0-DB",
                    "full_native_view": "T0-native-full",
                },
                "tau2_domain": {
                    "domain_db_sha256": self._domain_db_sha256,
                    "policy_sha256": self._policy_sha256,
                    "pre_environment_digest": pre_environment_digest,
                },
            },
            metadata={"benchmark": source_metadata},
            oracles=[],
        )
        return BenchmarkScenarioRecord(
            source_task_id=task_id,
            difficulty="unspecified",
            scenario=scenario,
            pre_environment_digest=pre_environment_digest,
            reference_tool_calls=reference_actions,
            ground_truth_output="",
            scorer=scorer,
        )


def _decode_json(value: bytes, label: str) -> Any:
    try:
        return json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BenchmarkConfigurationError(f"Invalid tau2 {label} JSON") from exc


def _read_optional_json(
    path: Optional[str | Path], label: str
) -> tuple[Any, Optional[str]]:
    if path is None:
        return None, None
    value = Path(path).read_bytes()
    return _decode_json(value, label), hashlib.sha256(value).hexdigest()


def _validate_sha256(value: str, field: str) -> str:
    normalized = str(value).casefold()
    if len(normalized) != 64 or any(
        char not in "0123456789abcdef" for char in normalized
    ):
        raise ValueError(f"{field} must be a SHA-256 digest")
    return normalized


def _optional_sha256(value: Optional[str], field: str) -> Optional[str]:
    return None if value is None else _validate_sha256(value, field)
