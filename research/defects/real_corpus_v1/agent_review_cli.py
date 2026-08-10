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
from research.defects.real_corpus_v1.agent_dispatch import (
    _load_object,
    _repo_root,
    _resolve_repo_path,
    validate_dispatch_record,
    validate_frozen_submission,
)


def _json_bytes(payload: Any) -> bytes:
    return (
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binding", type=Path, required=True)
    parser.add_argument("--dispatch-record", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.output.exists():
            raise ValueError("output directory must not already exist")
        dispatch = validate_dispatch_record(
            binding_path=args.binding,
            record_path=args.dispatch_record,
        )
        binding = _load_object(args.binding, "dispatch binding")
        repo = _repo_root(args.binding)
        inputs = binding["inputs"]
        packet_path = _resolve_repo_path(repo, inputs["packet"]["path"])
        protocol_path = _resolve_repo_path(repo, inputs["protocol"]["path"])
        submission_paths = {}
        for task_name, assignment in binding["canonical_task_mapping"].items():
            annotator_id = assignment["annotator_id"]
            submission_path = _resolve_repo_path(repo, assignment["output"])
            validate_frozen_submission(
                binding_path=args.binding,
                dispatch_record_path=args.dispatch_record,
                protocol_path=protocol_path,
                expected_annotator=annotator_id,
                template_path=_resolve_repo_path(
                    repo, inputs["templates"][annotator_id]["path"]
                ),
                submission_path=submission_path,
                expected_binding_sha256=dispatch["binding_sha256"],
                expected_freeze_payload_revision=dispatch[
                    "freeze_payload_revision"
                ],
                expected_start_workspace_sha256=dispatch[
                    "workspace_snapshot_sha256"
                ],
            )
            submission_paths[annotator_id] = submission_path
        if set(submission_paths) != {"A1", "A2", "A3"}:
            raise ValueError("binding must resolve exactly A1, A2, A3 submissions")
        packet_bytes = packet_path.read_bytes()
        packet = json.loads(packet_bytes)
        submission_bytes = {
            annotator_id: submission_paths[annotator_id].read_bytes()
            for annotator_id in ("A1", "A2", "A3")
        }
        analysis = analyze_agent_reviews(packet_bytes, submission_bytes)
        candidates = {
            candidate["defect_id"]: candidate
            for candidate in packet.get("candidates", [])
        }
        packet_parent = packet_path.parent
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
        agreement, human_packet = build_review_artifacts(
            packet_bytes,
            submission_bytes,
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
