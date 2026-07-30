"""Freeze a small, offline AgentDojo-to-Scenario conversion pilot."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from nanoharness.testing import AgentDojoBenchmarkAdapter


FROZEN_AT = datetime(2026, 7, 30, 8, 0, 0, tzinfo=timezone.utc)
SELECTIONS = {
    "workspace": ["user_task_0", "user_task_16"],
    "banking": ["user_task_1", "user_task_6"],
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parent
    parser.add_argument("--output-dir", type=Path, default=root / "raw")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Explicitly replace existing conversion artifacts",
    )
    args = parser.parse_args()
    outputs = {
        suite: args.output_dir / f"{suite}_manifest.json"
        for suite in SELECTIONS
    }
    summary_path = args.output_dir / "conversion_summary.json"
    existing = [path for path in [*outputs.values(), summary_path] if path.exists()]
    if existing and not args.overwrite:
        parser.error(
            "output artifacts already exist; choose another --output-dir or "
            "pass --overwrite"
        )

    adapter = AgentDojoBenchmarkAdapter.from_installed()
    manifests = {}
    for suite, task_ids in SELECTIONS.items():
        manifest = adapter.convert_user_tasks(
            suite,
            task_ids,
            manifest_id=f"agentdojo-v1.2.2-{suite}-conversion-pilot",
            frozen_at=FROZEN_AT,
            metadata={
                "pilot_role": "integration_validation",
                "selection_status": "development_not_held_out",
                "live_model": False,
                "executes_agentdojo_runtime": False,
            },
        )
        manifests[suite] = manifest

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for suite, manifest in manifests.items():
        outputs[suite].write_text(manifest.model_dump_json(indent=2) + "\n")
    summary = {
        "artifact_kind": "offline_benchmark_conversion_pilot",
        "source": adapter.source.model_dump(mode="json"),
        "benchmark_version": "v1.2.2",
        "frozen_at": FROZEN_AT.isoformat(),
        "live_model": False,
        "executes_agentdojo_runtime": False,
        "reproduces_agentdojo_results": False,
        "mutation_experiment": False,
        "suites": {
            suite: {
                "task_ids": SELECTIONS[suite],
                "task_count": len(manifest.tasks),
                "manifest_digest": manifest.manifest_digest,
                "oracle_binding": manifest.oracle_binding,
                "reference_tool_call_count": sum(
                    len(task.reference_tool_calls) for task in manifest.tasks
                ),
            }
            for suite, manifest in manifests.items()
        },
    }
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    )
    print(
        f"wrote {sum(len(item.tasks) for item in manifests.values())} "
        f"converted tasks across {len(manifests)} suites to {args.output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
