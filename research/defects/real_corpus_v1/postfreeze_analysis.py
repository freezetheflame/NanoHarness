"""Correct raw metadata serialization after bound agent-review validation."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from research.defects.real_corpus_v1.agent_dispatch import (
    _load_object,
    _repo_root,
    _resolve_repo_path,
    validate_dispatch_record,
    validate_frozen_submission,
)
from research.defects.real_corpus_v1.agent_review import (
    AGENT_IDS,
    analyze_agent_reviews,
    build_review_artifacts,
)


def _json_bytes(payload: Any) -> bytes:
    return (
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _without_source_metadata(packet: Mapping[str, Any]) -> dict[str, Any]:
    stripped = deepcopy(packet)
    for agent_id in AGENT_IDS:
        source = stripped["source_submissions"][agent_id]
        source.pop("provenance")
        source.pop("completion")
    return stripped


def build_postfreeze_artifacts(
    packet_bytes: bytes,
    submission_bytes: Mapping[str, bytes],
    *,
    patch_payloads: Mapping[str, bytes] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Preserve validated raw metadata without changing bound review results."""

    agreement, bound_packet = build_review_artifacts(
        packet_bytes,
        submission_bytes,
        patch_payloads=patch_payloads,
    )
    corrected_packet = deepcopy(bound_packet)
    for agent_id in AGENT_IDS:
        raw_submission = json.loads(submission_bytes[agent_id])
        source = corrected_packet["source_submissions"][agent_id]
        source["provenance"] = deepcopy(raw_submission["agent_provenance"])
        source["completion"] = deepcopy(raw_submission["completion"])

    if _without_source_metadata(corrected_packet) != _without_source_metadata(
        bound_packet
    ):
        raise ValueError("post-freeze correction changed non-metadata packet content")
    return agreement, corrected_packet


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
        for assignment in binding["canonical_task_mapping"].values():
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
        if set(submission_paths) != set(AGENT_IDS):
            raise ValueError("binding must resolve exactly A1, A2, A3 submissions")

        packet_bytes = packet_path.read_bytes()
        packet = json.loads(packet_bytes)
        submission_bytes = {
            agent_id: submission_paths[agent_id].read_bytes()
            for agent_id in AGENT_IDS
        }
        patch_payloads = _selected_patch_payloads(
            packet_path,
            packet,
            packet_bytes,
            submission_bytes,
        )
        bound_agreement, bound_packet = build_review_artifacts(
            packet_bytes,
            submission_bytes,
            patch_payloads=patch_payloads,
        )
        corrected_agreement, corrected_packet = build_postfreeze_artifacts(
            packet_bytes,
            submission_bytes,
            patch_payloads=patch_payloads,
        )
        if corrected_agreement != bound_agreement:
            raise ValueError("post-freeze correction changed agreement results")
        if _without_source_metadata(corrected_packet) != _without_source_metadata(
            bound_packet
        ):
            raise ValueError("post-freeze correction changed bound packet content")

        artifacts = {
            "human_audit_packet.json": _json_bytes(corrected_packet),
            "pass_a_agreement.json": _json_bytes(corrected_agreement),
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


def _selected_patch_payloads(
    packet_path: Path,
    packet: Mapping[str, Any],
    packet_bytes: bytes,
    submission_bytes: Mapping[str, bytes],
) -> dict[str, bytes]:
    agreement = analyze_agent_reviews(packet_bytes, submission_bytes)
    candidates = {
        candidate["defect_id"]: candidate for candidate in packet.get("candidates", [])
    }
    packet_parent = packet_path.parent
    patch_payloads = {}
    for defect_id in agreement["human_audit_selection"]["reasons_by_defect_id"]:
        candidate = candidates[defect_id]
        patch_path = candidate.get("patch_path")
        if not isinstance(patch_path, str) or not patch_path:
            raise ValueError(f"{defect_id} selected candidate requires patch_path")
        resolved_patch = (packet_parent / patch_path).resolve()
        try:
            resolved_patch.relative_to(packet_parent)
        except ValueError as error:
            raise ValueError(f"{defect_id} patch_path escapes packet directory") from error
        patch_payloads[defect_id] = resolved_patch.read_bytes()
    return patch_payloads


if __name__ == "__main__":
    raise SystemExit(main())
