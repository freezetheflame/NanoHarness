"""Build the complete candidate index and deterministic blind coding packet."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.defects.real_corpus_v1.build_packets import (
    PacketOutputs,
    build_packets,
)
from research.defects.real_corpus_v1.pipeline import select_and_partition


@dataclass(frozen=True)
class CandidateIndexOutputs:
    candidate_universe: Path
    selected_private: Path
    packet: PacketOutputs


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def build_candidate_index(
    raw_dir: Path,
    manifest: Mapping[str, Any],
    output_dir: Path,
) -> CandidateIndexOutputs:
    """Combine every declared pinned history before sampling by repository."""

    candidates = []
    sources = []
    for repository in manifest["repositories"]:
        path = raw_dir / repository.replace("/", "__") / "git_history.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("repository") != repository:
            raise ValueError(f"history repository mismatch for {repository}")
        records = payload.get("candidates")
        if not isinstance(records, list) or payload.get("candidate_count") != len(records):
            raise ValueError(f"history candidate count mismatch for {repository}")
        candidates.extend(records)
        sources.append({
            "repository": repository,
            "pinned_commit": payload["pinned_commit"],
            "candidate_count": len(records),
            "source": str(path.relative_to(raw_dir.parent)).replace("\\", "/"),
        })

    candidate_universe = output_dir / "candidate_universe.json"
    _write_json(candidate_universe, {
        "schema_version": 1,
        "manifest_id": manifest["manifest_id"],
        "candidate_count": len(candidates),
        "sources": sources,
        "candidates": sorted(
            candidates,
            key=lambda item: (
                item["repository"].lower(),
                item["canonical_locator"],
            ),
        ),
    })
    selected = select_and_partition(
        candidates,
        cap_per_repository=manifest["cap_per_repository"],
    )
    selected_private = output_dir / "selected_candidates.private.json"
    _write_json(selected_private, {
        "schema_version": 1,
        "manifest_id": manifest["manifest_id"],
        "candidate_count": len(selected),
        "candidates": selected,
    })
    packet = build_packets(selected, output_dir)
    return CandidateIndexOutputs(
        candidate_universe=candidate_universe,
        selected_private=selected_private,
        packet=packet,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    outputs = build_candidate_index(args.raw, manifest, args.output)
    print(json.dumps({
        "candidate_universe": str(outputs.candidate_universe),
        "selected_private": str(outputs.selected_private),
        "evidence_packet": str(outputs.packet.evidence_packet),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
