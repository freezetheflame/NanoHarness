"""Run the frozen AgentDojo native Tool-fault Campaign."""

from __future__ import annotations

import argparse
from pathlib import Path

from agentdojo.agent_pipeline.ground_truth_pipeline import GroundTruthPipeline

from nanoharness.testing import (
    AgentDojoFaultAdapterFactory,
    BenchmarkManifest,
    FaultExperimentManifest,
    SubjectFaultCampaignRunner,
    SubjectIdentity,
)


def _benchmark(root: Path) -> BenchmarkManifest:
    path = (
        root.parent
        / "agentdojo_offline_conversion"
        / "raw"
        / "banking_manifest.json"
    )
    return BenchmarkManifest.model_validate_json(path.read_text())


def _identity(benchmark: BenchmarkManifest) -> SubjectIdentity:
    return SubjectIdentity(
        subject_id="agentdojo-ground-truth-banking@0.1.35",
        runtime="agentdojo",
        version=benchmark.source.package_version,
        revision=benchmark.source.revision,
        source_url=benchmark.source.source_url,
        independently_developed=True,
        metadata={
            "pipeline": "agentdojo.agent_pipeline.GroundTruthPipeline",
            "suite": "banking",
            "purpose": "native_tool_fault_integration_validation",
            "live_model": False,
        },
    )


def _factory(root: Path) -> AgentDojoFaultAdapterFactory:
    benchmark = _benchmark(root)
    return AgentDojoFaultAdapterFactory(
        _identity(benchmark),
        benchmark,
        lambda scenario, task: GroundTruthPipeline(task),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parent
    parser.add_argument("--manifest", type=Path, default=root / "manifest.json")
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "raw" / "fault_campaign.json",
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.output.exists() and not args.overwrite:
        parser.error(
            f"output {args.output} already exists; choose another path or "
            "pass --overwrite"
        )
    manifest = FaultExperimentManifest.model_validate_json(
        args.manifest.read_text()
    )
    report = SubjectFaultCampaignRunner(_factory(root)).run(manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report.model_dump_json(indent=2) + "\n")
    print(
        f"wrote {len(report.campaign.outcomes)} fault outcomes to "
        f"{args.output}; killed={report.campaign.killed}, "
        f"survived={report.campaign.survived}, "
        f"score={report.campaign.mutation_score}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
