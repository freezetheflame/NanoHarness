"""Create the deterministic Manifest for the AgentDojo scorer bridge pilot."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from agentdojo.agent_pipeline.ground_truth_pipeline import GroundTruthPipeline

from nanoharness.testing import (
    AgentDojoSubjectAdapter,
    BenchmarkManifest,
    ExperimentCell,
    ExperimentManifest,
    experiment_manifest_digest,
)
from run import _identity


FROZEN_AT = datetime(2026, 7, 30, 10, 0, 0, tzinfo=timezone.utc)
HARNESS_REVISION = "b693aaa924ae1c0694f40fd9064982a633893206"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parent
    source_root = root.parent / "agentdojo_offline_conversion" / "raw"
    parser.add_argument("--output", type=Path, default=root / "manifest.json")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.output.exists() and not args.overwrite:
        parser.error(f"output {args.output} exists; pass --overwrite")

    subjects = []
    cells = []
    source_digests = {}
    for suite in ("workspace", "banking"):
        benchmark = BenchmarkManifest.model_validate_json(
            (source_root / f"{suite}_manifest.json").read_text()
        )
        identity = _identity(benchmark.source, suite)
        adapter = AgentDojoSubjectAdapter.from_installed(
            identity,
            benchmark,
            lambda scenario, task: GroundTruthPipeline(task),
        )
        subjects.append(identity)
        source_digests[suite] = benchmark.manifest_digest
        cells.extend(
            ExperimentCell(
                cell_id=f"{suite}-{record.source_task_id}",
                subject_id=identity.subject_id,
                scenario=adapter.scenario_for(record.source_task_id),
                seeds=[0],
            )
            for record in benchmark.tasks
        )
    metadata = {
        "pilot_role": "integration_validation",
        "selection_status": "development_not_held_out",
        "pipeline": "AgentDojo GroundTruthPipeline",
        "scorer": "original AgentDojo user-task utility",
        "live_model": False,
        "mutation_experiment": False,
        "harness_revision": HARNESS_REVISION,
        "source_benchmark_manifest_digests": source_digests,
    }
    experiment_id = "agentdojo-v1.2.2-original-scorer-bridge-pilot"
    digest = experiment_manifest_digest(
        experiment_id,
        subjects,
        cells,
        frozen_at=FROZEN_AT,
        metadata=metadata,
    )
    manifest = ExperimentManifest(
        experiment_id=experiment_id,
        subjects=subjects,
        cells=cells,
        frozen_at=FROZEN_AT,
        manifest_digest=digest,
        metadata=metadata,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(manifest.model_dump_json(indent=2) + "\n")
    print(
        f"wrote {len(cells)} cells to {args.output}; "
        f"manifest_digest={manifest.manifest_digest}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
