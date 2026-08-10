"""Build agreement results and a blind human-audit packet from three agents."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from research.defects.real_corpus_v1.agent_review import (
    analyze_agent_reviews,
    build_review_artifacts,
)


def _json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--a1", type=Path, required=True)
    parser.add_argument("--a2", type=Path, required=True)
    parser.add_argument("--a3", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.output.exists():
            raise ValueError("output directory must not already exist")
        packet_bytes = args.packet.read_bytes()
        packet = json.loads(packet_bytes)
        submissions = {
            "A1": json.loads(args.a1.read_text(encoding="utf-8")),
            "A2": json.loads(args.a2.read_text(encoding="utf-8")),
            "A3": json.loads(args.a3.read_text(encoding="utf-8")),
        }
        analysis = analyze_agent_reviews(packet_bytes, packet, submissions)
        candidates = {
            candidate["defect_id"]: candidate
            for candidate in packet.get("candidates", [])
        }
        packet_parent = args.packet.resolve().parent
        patch_payloads = {}
        for defect_id in analysis["human_audit_selection"][
            "reasons_by_defect_id"
        ]:
            candidate = candidates[defect_id]
            patch_path = candidate.get("patch_path")
            if not isinstance(patch_path, str) or not patch_path:
                raise ValueError(
                    f"{defect_id} selected candidate requires patch_path"
                )
            resolved_patch = (packet_parent / patch_path).resolve()
            try:
                resolved_patch.relative_to(packet_parent)
            except ValueError as error:
                raise ValueError(
                    f"{defect_id} patch_path escapes packet directory"
                ) from error
            patch_payloads[defect_id] = resolved_patch.read_bytes()
        raw_submission_digests = {
            "A1": hashlib.sha256(args.a1.read_bytes()).hexdigest(),
            "A2": hashlib.sha256(args.a2.read_bytes()).hexdigest(),
            "A3": hashlib.sha256(args.a3.read_bytes()).hexdigest(),
        }
        agreement, human_packet = build_review_artifacts(
            packet_bytes,
            packet,
            submissions,
            raw_submission_digests=raw_submission_digests,
            patch_payloads=patch_payloads,
        )
        artifacts = {
            "human_audit_packet.json": _json_bytes(human_packet),
            "pass_a_agreement.json": _json_bytes(agreement),
        }
        checksums = "".join(
            f"{hashlib.sha256(payload).hexdigest()}  {name}\n"
            for name, payload in artifacts.items()
        ).encode("utf-8")
        args.output.mkdir(parents=True)
        for name, payload in artifacts.items():
            (args.output / name).write_bytes(payload)
        (args.output / "SHA256SUMS").write_bytes(checksums)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
