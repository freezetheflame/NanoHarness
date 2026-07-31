"""Run the deterministic tau2 native grader/state bridge plumbing Pilot."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import tau2
from tau2.data_model.message import AssistantMessage, ToolCall, UserMessage
from tau2.data_model.simulation import SimulationRun, TerminationReason
from tau2.domains.retail.environment import get_environment
from tau2.domains.retail.utils import (
    RETAIL_DB_PATH,
    RETAIL_POLICY_PATH,
    RETAIL_TASK_SET_PATH,
)

from nanoharness.testing import (
    TAU2_REVISION,
    BenchmarkManifest,
    ScenarioReport,
    SubjectIdentity,
    Tau2BenchmarkAdapter,
    Tau2SubjectAdapter,
)


SELECTED_TASK_IDS = ["62", "65", "7", "37", "96", "95", "10", "50"]
FROZEN_AT = datetime(2026, 7, 31, tzinfo=timezone.utc)


def reference_simulation(scenario, task, recorder):
    """Execute the non-normative reference plan as bridge plumbing only."""

    initial = task.initial_state
    messages = list(initial.message_history or []) if initial else []
    environment = get_environment(solo_mode=False)
    environment.set_state(
        initialization_data=(initial.initialization_data if initial else None),
        initialization_actions=(initial.initialization_actions if initial else None),
        message_history=list(messages),
        strict=True,
    )
    for action in task.evaluation_criteria.actions or []:
        call = ToolCall(
            id=action.action_id,
            name=action.name,
            arguments=action.arguments,
            requestor=action.requestor,
        )
        message_class = UserMessage if action.requestor == "user" else AssistantMessage
        messages.append(
            message_class(role=action.requestor, content=None, tool_calls=[call])
        )
        messages.append(environment.get_response(call))
    messages.append(AssistantMessage(role="assistant", content="Reference complete."))
    return SimulationRun(
        id=f"nanoharness-reference-{task.id}",
        task_id=task.id,
        start_time="2026-07-31T00:00:00Z",
        end_time="2026-07-31T00:00:01Z",
        duration=1.0,
        termination_reason=TerminationReason.AGENT_STOP,
        messages=messages,
        seed=scenario.seed,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    source_root = Path(tau2.__file__).resolve().parents[2]
    revision = subprocess.run(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if revision != TAU2_REVISION:
        raise RuntimeError(
            f"tau2 source revision mismatch: expected {TAU2_REVISION}, got {revision}"
        )
    version = importlib.metadata.version("tau2")
    if version != "1.0.1":
        raise RuntimeError(f"tau2 version mismatch: expected 1.0.1, got {version}")

    manifest = Tau2BenchmarkAdapter.from_files(
        RETAIL_TASK_SET_PATH,
        domain_db_path=RETAIL_DB_PATH,
        policy_path=RETAIL_POLICY_PATH,
    ).convert_tasks(
        "retail",
        SELECTED_TASK_IDS,
        manifest_id="tau2-v1.0.1-retail-native-bridge-pilot",
        frozen_at=FROZEN_AT,
        selection_provenance={
            "seed": "icst27-pilot-v0",
            "strata_order": [
                "read_only",
                "single_update",
                "multi_update",
                "handoff",
            ],
            "task_ids": SELECTED_TASK_IDS,
        },
        metadata={
            "status": "engineering-pilot",
            "execution": "non-normative-reference-trajectory-plumbing",
        },
    )
    identity = SubjectIdentity(
        subject_id="tau2-reference-trajectory@v1.0.1",
        runtime="tau2",
        version=version,
        revision=revision,
        source_url="https://github.com/sierra-research/tau2-bench",
        independently_developed=True,
        metadata={
            "execution": "reference-trajectory-plumbing",
            "python": sys.version.split()[0],
        },
    )
    adapter = Tau2SubjectAdapter.from_installed(
        identity,
        manifest,
        reference_simulation,
    )
    reports = [
        adapter.run(adapter.scenario_for(task_id, seed=0))
        for task_id in SELECTED_TASK_IDS
    ]

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "benchmark_manifest.json": _json_bytes(manifest.model_dump(mode="json")),
        "scenario_reports.json": _json_bytes(
            [report.model_dump(mode="json") for report in reports]
        ),
        "summary.json": _json_bytes(
            _summary(manifest, reports, revision=revision, python=sys.version.split()[0])
        ),
    }
    for name, value in files.items():
        (output_dir / name).write_bytes(value)
    checksum_lines = [
        f"{hashlib.sha256(value).hexdigest()}  {name}"
        for name, value in sorted(files.items())
    ]
    (output_dir / "SHA256SUMS").write_text("\n".join(checksum_lines) + "\n")
    print(json.dumps(_summary(manifest, reports, revision=revision, python=sys.version.split()[0]), indent=2))


def _summary(
    manifest: BenchmarkManifest,
    reports: list[ScenarioReport],
    *,
    revision: str,
    python: str,
) -> dict:
    return {
        "status": "engineering-pilot",
        "claim_boundary": (
            "Reference trajectories validate native grader/state/ledger plumbing; "
            "they are not agent-performance or confirmatory mutation evidence."
        ),
        "live_model": False,
        "tau2_version": "1.0.1",
        "tau2_revision": revision,
        "python": python,
        "manifest_digest": manifest.manifest_digest,
        "task_ids": SELECTED_TASK_IDS,
        "report_count": len(reports),
        "passed_count": sum(report.passed for report in reports),
        "t0_db_passed_count": sum(report.verdicts[0].passed for report in reports),
        "state_delta_passed_count": sum(
            report.verdicts[1].passed for report in reports
        ),
        "side_effects_passed_count": sum(
            report.verdicts[2].passed for report in reports
        ),
    }


def _json_bytes(value) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


if __name__ == "__main__":
    main()
