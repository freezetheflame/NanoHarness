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

from research.defects.real_corpus_v1.agent_review import build_review_artifacts


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
        agreement, human_packet = build_review_artifacts(
            packet_bytes, packet, submissions
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
