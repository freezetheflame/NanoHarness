"""Validate a blind human defect-coding submission against its packet."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Optional, Sequence

from nanoharness.testing.defects import DefectCodingSubmission


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", type=Path)
    parser.add_argument("submission", type=Path)
    parser.add_argument("--require-complete", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    packet_bytes = args.packet.read_bytes()
    packet_digest = hashlib.sha256(packet_bytes).hexdigest()
    packet = json.loads(packet_bytes)
    submission = DefectCodingSubmission.model_validate_json(
        args.submission.read_text(encoding="utf-8")
    )
    packet_ids = {
        item["defect_id"] for item in packet.get("candidates", [])
    }
    submission_ids = {item.defect_id for item in submission.entries}
    errors = []
    if submission.packet_sha256 != packet_digest:
        errors.append("packet digest mismatch")
    if args.require_complete and submission_ids != packet_ids:
        errors.append("submission IDs do not exactly match packet IDs")
    if args.require_complete and submission.completion is None:
        errors.append("completion declaration is required")
    print(json.dumps({"valid": not errors, "errors": errors}, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
