import hashlib
import json
from datetime import datetime, timezone

import pytest

from nanoharness.testing import (
    BenchmarkConfigurationError,
    BenchmarkManifest,
    BenchmarkSourceIdentity,
    TAU2_REVISION,
    Tau2BenchmarkAdapter,
)


TASKS = [
    {
        "id": "7",
        "initial_state": None,
        "user_scenario": {
            "instructions": {
                "domain": "retail",
                "reason_for_call": "Exchange one item.",
                "known_info": "Known customer",
                "unknown_info": "Unknown order",
                "task_instructions": ".",
            }
        },
        "evaluation_criteria": {
            "actions": [
                {
                    "name": "exchange_item",
                    "arguments": {"order_id": "#1"},
                    "action_id": "7_0",
                }
            ],
            "reward_basis": ["DB", "NL_ASSERTION"],
            "nl_assertions": None,
        },
        "issues": [{"id": "issue-7", "status": "open"}],
    },
    {
        "id": "62",
        "initial_state": None,
        "user_scenario": {
            "instructions": {
                "domain": "retail",
                "reason_for_call": "Ask for the price.",
            }
        },
        "evaluation_criteria": {
            "actions": [],
            "reward_basis": ["DB", "NL_ASSERTION"],
            "nl_assertions": ["The agent states the price."],
        },
    },
]


def _source(distribution="tau2"):
    return BenchmarkSourceIdentity(
        benchmark_id="tau2@test",
        distribution=distribution,
        package_version="test",
        revision="0123456789abcdef",
        source_url="https://github.com/sierra-research/tau2-bench",
        license="MIT",
    )


def _adapter():
    return Tau2BenchmarkAdapter(
        _source(),
        TASKS,
        tasks_sha256="1" * 64,
        domain_db={"orders": {"#1": {"status": "delivered"}}},
        domain_db_sha256="2" * 64,
        policy="Policy text",
        policy_sha256="3" * 64,
        splits={"train": ["7"], "test": ["62"], "base": ["7", "62"]},
        splits_sha256="4" * 64,
    )


def test_tau2_conversion_preserves_reward_issue_split_and_loss_boundary():
    frozen_at = datetime(2026, 7, 30, tzinfo=timezone.utc)

    manifest = _adapter().convert_tasks(
        "retail",
        ["7", "62"],
        frozen_at=frozen_at,
        selection_provenance={"seed": "icst27-pilot-v0"},
    )

    assert manifest.oracle_binding == "unbound"
    assert manifest.metadata["reward_views"] == ["T0-DB", "T0-native-full"]
    assert manifest.metadata["selection_provenance"]["seed"] == (
        "icst27-pilot-v0"
    )
    task_7, task_62 = manifest.tasks
    assert task_7.scenario.query == "Exchange one item."
    assert task_7.scenario.metadata["benchmark"]["issue_status"] == "unresolved"
    assert task_7.scenario.fixtures["tau2_task"]["split_membership"] == [
        "base",
        "train",
    ]
    assert task_7.scenario.fixtures["tau2_task"]["unresolved_issue_ids"] == [
        "issue-7"
    ]
    assert task_7.reference_tool_calls[0]["name"] == "exchange_item"
    assert task_7.scenario.fixtures["tau2_reference"]["semantics"] == (
        "non_normative_reference_plan"
    )
    assert task_7.scenario.fixtures["tau2_reward"]["requires_llm_judge"] is False
    assert task_62.scenario.fixtures["tau2_reward"]["requires_llm_judge"] is True
    assert task_62.scenario.fixtures["tau2_task"]["split_membership"] == [
        "base",
        "test",
    ]
    assert task_7.scenario.oracles == []
    manifest.assert_unchanged()
    assert BenchmarkManifest.model_validate_json(manifest.model_dump_json()) == manifest


def test_tau2_conversion_rejects_invalid_selection_and_domain():
    adapter = _adapter()

    with pytest.raises(BenchmarkConfigurationError, match="at least one"):
        adapter.convert_tasks("retail", [])
    with pytest.raises(BenchmarkConfigurationError, match="duplicate"):
        adapter.convert_tasks("retail", ["7", "7"])
    with pytest.raises(BenchmarkConfigurationError, match="Unknown"):
        adapter.convert_tasks("retail", ["999"])
    with pytest.raises(BenchmarkConfigurationError, match="belongs"):
        adapter.convert_tasks("airline", ["7"])


def test_tau2_conversion_retains_explicit_benchmark_version():
    manifest = _adapter().convert_tasks(
        "retail",
        ["7"],
        benchmark_version="v-test",
    )

    assert manifest.benchmark_version == "v-test"
    assert manifest.tasks[0].scenario.scenario_id == "tau2-v-test-retail-7"
    assert manifest.tasks[0].scenario.metadata["benchmark"]["benchmark_version"] == (
        "v-test"
    )


def test_tau2_from_files_binds_exact_hashes_and_pinned_source(tmp_path):
    tasks_bytes = json.dumps(TASKS, sort_keys=True).encode()
    tasks_path = tmp_path / "tasks.json"
    db_path = tmp_path / "db.json"
    policy_path = tmp_path / "policy.md"
    splits_path = tmp_path / "split_tasks.json"
    tasks_path.write_bytes(tasks_bytes)
    db_path.write_text('{"orders": {}}')
    policy_path.write_text("Pinned policy")
    splits_path.write_text('{"base": ["7", "62"]}')
    tasks_digest = hashlib.sha256(tasks_bytes).hexdigest()

    adapter = Tau2BenchmarkAdapter.from_files(
        tasks_path,
        expected_tasks_sha256=tasks_digest,
        domain_db_path=db_path,
        policy_path=policy_path,
        splits_path=splits_path,
    )
    manifest = adapter.convert_tasks("retail", ["7"])

    assert adapter.source.revision == TAU2_REVISION
    assert adapter.source.metadata["python_requires"] == ">=3.12,<3.14"
    assert manifest.metadata["tasks_sha256"] == tasks_digest
    assert manifest.metadata["domain_db_sha256"] == hashlib.sha256(
        db_path.read_bytes()
    ).hexdigest()

    with pytest.raises(BenchmarkConfigurationError, match="checksum mismatch"):
        Tau2BenchmarkAdapter.from_files(
            tasks_path,
            expected_tasks_sha256="0" * 64,
        )


def test_tau2_adapter_rejects_non_tau2_identity_and_bad_source():
    with pytest.raises(ValueError, match="distribution"):
        Tau2BenchmarkAdapter(_source("other"), TASKS, tasks_sha256="1" * 64)
    with pytest.raises(BenchmarkConfigurationError, match="requires an ID"):
        Tau2BenchmarkAdapter(_source(), [{}], tasks_sha256="1" * 64)
