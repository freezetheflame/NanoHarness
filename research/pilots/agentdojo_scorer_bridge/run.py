"""Run the frozen AgentDojo original-scorer bridge pilot."""

from __future__ import annotations

import argparse
from pathlib import Path

from agentdojo.agent_pipeline.ground_truth_pipeline import GroundTruthPipeline

from nanoharness.testing import (
    AgentDojoSubjectAdapter,
    BenchmarkManifest,
    ExperimentManifest,
    ExperimentRunner,
    SubjectIdentity,
)


def _identity(source, suite: str) -> SubjectIdentity:
    return SubjectIdentity(
        subject_id=f"agentdojo-ground-truth-{suite}@{source.package_version}",
        runtime="agentdojo",
        version=source.package_version,
        revision=source.revision,
        source_url=source.source_url,
        independently_developed=True,
        metadata={
            "pipeline": "agentdojo.agent_pipeline.GroundTruthPipeline",
            "suite": suite,
            "purpose": "original_scorer_bridge_integration_validation",
            "live_model": False,
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parent
    source_root = root.parent / "agentdojo_offline_conversion" / "raw"
    parser.add_argument("--manifest", type=Path, default=root / "manifest.json")
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "raw" / "experiment_report.json",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Explicitly replace an existing execution artifact",
    )
    args = parser.parse_args()
    if args.output.exists() and not args.overwrite:
        parser.error(
            f"output {args.output} already exists; choose another path or "
            "pass --overwrite"
        )

    experiment = ExperimentManifest.model_validate_json(args.manifest.read_text())
    adapters = []
    for suite in ("workspace", "banking"):
        benchmark = BenchmarkManifest.model_validate_json(
            (source_root / f"{suite}_manifest.json").read_text()
        )
        adapters.append(
            AgentDojoSubjectAdapter.from_installed(
                _identity(benchmark.source, suite),
                benchmark,
                lambda scenario, task: GroundTruthPipeline(task),
            )
        )
    report = ExperimentRunner(adapters).run(experiment)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report.model_dump_json(indent=2) + "\n")
    print(
        f"wrote {len(report.observations)} scorer-bound observations to "
        f"{args.output}; passed={sum(item.report.passed for item in report.observations)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
