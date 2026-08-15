"""Immutable, separate response validation for the frozen human audit packet."""

from __future__ import annotations

import hashlib
import json
import os
import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = 1
AUDIT_PACKET_SHA256 = "8bfb7bc288de2890af2268978b0efa8cc69820081ee4151d8861f5586f4d369c"
DEFAULT_AUDIT_PACKET_PATH = (
    Path(__file__).parent
    / "formal"
    / "agent-review-v1"
    / "analysis"
    / "human_audit_packet.json"
)
SEALED_FORMAL_ROOT = DEFAULT_AUDIT_PACKET_PATH.parents[1]
PRIVATE_ROOT = Path(__file__).parent / "private"
BOUNDARIES = {
    "model", "tool", "context", "state", "hook", "evaluator", "permission",
    "control_flow", "replay", "other",
}
EXCLUSION_REASONS = {
    "doc_or_format_only", "feature_request", "insufficient_public_evidence",
    "model_quality_only", "not_agent_boundary", "refactor_only",
    "dependency_only", "benchmark_difficulty", "duplicate", "out_of_scope",
}
DISPOSITIONS = {"confirm", "override", "disputed"}
DECISIONS = {"include", "exclude", "uncertain"}
RESPONSE_FIELDS = {"schema_version", "audit_packet_sha256", "reviews"}
REVIEW_FIELDS = {
    "defect_id", "selection_reasons", "reviewer_id", "reviewed_at",
    "evidence_considered", "disposition", "final_decision",
    "final_exclusion_reason", "final_boundaries", "final_trigger",
    "final_symptom", "final_root_cause", "final_impact", "final_rationale",
}
JUDGMENT_FIELDS = REVIEW_FIELDS - {"defect_id", "selection_reasons"}


def sha256_file(path: str | Path) -> str:
    """Return the SHA-256 digest of bytes without rewriting the path."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def ensure_external_output_path(path: str | Path) -> Path:
    """Resolve an output path and reject the sealed formal and private trees."""
    resolved = Path(path).resolve()
    if resolved.is_relative_to(SEALED_FORMAL_ROOT.resolve()):
        raise ValueError("output path must be external to the sealed formal snapshot")
    if resolved.is_relative_to(PRIVATE_ROOT.resolve()):
        raise ValueError("output path must be external to the private material tree")
    return resolved


def _empty_review(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "defect_id": candidate["defect_id"],
        "selection_reasons": list(candidate["selection_reasons"]),
        "reviewer_id": None,
        "reviewed_at": None,
        "evidence_considered": [],
        "disposition": None,
        "final_decision": None,
        "final_exclusion_reason": "",
        "final_boundaries": [],
        "final_trigger": "",
        "final_symptom": "",
        "final_root_cause": "",
        "final_impact": "",
        "final_rationale": "",
    }


def initialize_empty_response(
    audit_packet: Mapping[str, Any], *, audit_packet_sha256: str
) -> dict[str, Any]:
    """Create a draft response without changing or embedding the source packet."""
    candidates = _packet_candidates(audit_packet)
    return {
        "schema_version": SCHEMA_VERSION,
        "audit_packet_sha256": audit_packet_sha256,
        "reviews": [_empty_review(candidate) for candidate in candidates],
    }


def load_pinned_audit_packet() -> dict[str, Any]:
    """Read only the public frozen audit packet and enforce its byte digest."""
    packet_bytes = DEFAULT_AUDIT_PACKET_PATH.read_bytes()
    if hashlib.sha256(packet_bytes).hexdigest() != AUDIT_PACKET_SHA256:
        raise ValueError("pinned audit packet SHA-256 does not match AUDIT_PACKET_SHA256")
    try:
        packet = json.loads(packet_bytes)
    except json.JSONDecodeError as exc:
        raise ValueError("pinned audit packet is not valid JSON") from exc
    if not isinstance(packet, dict):
        raise ValueError("pinned audit packet must be a JSON object")
    _packet_candidates(packet)
    return packet


def initialize_pinned_empty_response() -> dict[str, Any]:
    """Create the only production draft: one bound to the frozen public packet."""
    return initialize_empty_response(
        load_pinned_audit_packet(), audit_packet_sha256=AUDIT_PACKET_SHA256
    )


def _packet_candidates(audit_packet: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    candidates = audit_packet.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("audit packet candidates must be a list")
    ids = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, Mapping):
            raise ValueError(f"audit packet candidate {index} must be an object")
        if not isinstance(candidate.get("defect_id"), str):
            raise ValueError(f"audit packet candidate {index} defect_id is required")
        if not isinstance(candidate.get("selection_reasons"), list):
            raise ValueError(f"audit packet {candidate['defect_id']} selection_reasons must be a list")
        ids.append(candidate["defect_id"])
    if len(ids) != len(set(ids)):
        raise ValueError("audit packet defect IDs must be unique")
    return candidates


def _unexpected_fields(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    extras = set(value) - allowed
    if extras:
        raise ValueError(f"{label} has unexpected field: {sorted(extras)[0]}")
    missing = allowed - set(value)
    if missing:
        raise ValueError(f"{label} missing field: {sorted(missing)[0]}")


def _require_string(review: Mapping[str, Any], field: str, defect_id: str) -> None:
    if not isinstance(review.get(field), str) or not review[field].strip():
        raise ValueError(f"{defect_id} {field} is required")


def _validate_timestamp(value: Any, defect_id: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{defect_id} reviewed_at is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{defect_id} reviewed_at must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{defect_id} reviewed_at must be timezone-aware")


def validate_review(
    review: Mapping[str, Any], candidate: Mapping[str, Any], *, complete: bool
) -> None:
    """Validate a response record without inferring or changing a judgment."""
    defect_id = candidate.get("defect_id", "<unknown>")
    if not isinstance(review, Mapping):
        raise ValueError(f"{defect_id} review must be an object")
    _unexpected_fields(review, REVIEW_FIELDS, str(defect_id))
    if review["defect_id"] != defect_id:
        raise ValueError(f"{defect_id} defect_id must exactly match source")
    if review["selection_reasons"] != candidate.get("selection_reasons"):
        raise ValueError(f"{defect_id} selection_reasons must exactly match source")
    if not complete:
        expected = _empty_review(candidate)
        if dict(review) != expected:
            raise ValueError(f"{defect_id} draft review must remain empty")
        return

    _require_string(review, "reviewer_id", defect_id)
    _validate_timestamp(review["reviewed_at"], defect_id)
    evidence = review["evidence_considered"]
    allowed_evidence = {
        item.get("evidence_id")
        for item in candidate.get("candidate", {}).get("evidence", [])
        if isinstance(item, Mapping)
    }
    if not isinstance(evidence, list) or not evidence or len(evidence) != len(set(evidence)) or not set(evidence) <= allowed_evidence:
        raise ValueError(f"{defect_id} evidence_considered must be nonempty unique source evidence IDs")
    if review["disposition"] not in DISPOSITIONS:
        raise ValueError(f"{defect_id} disposition must be one of {sorted(DISPOSITIONS)}")
    if review["final_decision"] not in DECISIONS:
        raise ValueError(f"{defect_id} final_decision must be one of {sorted(DECISIONS)}")
    boundaries = review["final_boundaries"]
    if not isinstance(boundaries, list) or len(boundaries) != len(set(boundaries)) or not set(boundaries) <= BOUNDARIES:
        raise ValueError(f"{defect_id} final_boundaries must contain unique allowed boundaries")
    exclusion = review["final_exclusion_reason"]
    if not isinstance(exclusion, str):
        raise ValueError(f"{defect_id} final_exclusion_reason must be a string")
    for field in ("final_trigger", "final_symptom", "final_root_cause", "final_impact", "final_rationale"):
        _require_string(review, field, defect_id)
    if review["disposition"] == "disputed":
        if review["final_decision"] != "uncertain":
            raise ValueError(f"{defect_id} disputed requires final_decision uncertain")
        if boundaries or exclusion.strip():
            raise ValueError(f"{defect_id} disputed requires empty boundaries and final_exclusion_reason")
    elif review["final_decision"] == "include":
        if not boundaries:
            raise ValueError(f"{defect_id} include requires final_boundaries")
        if exclusion.strip():
            raise ValueError(f"{defect_id} include requires empty final_exclusion_reason")
    elif review["final_decision"] == "exclude":
        if boundaries:
            raise ValueError(f"{defect_id} exclude requires empty final_boundaries")
        if exclusion not in EXCLUSION_REASONS:
            raise ValueError(f"{defect_id} exclude requires an allowed final_exclusion_reason")
    elif boundaries or exclusion.strip():
        raise ValueError(f"{defect_id} uncertain requires empty boundaries and final_exclusion_reason")


def _validate_response_header(
    response: Mapping[str, Any], audit_packet_sha256: str
) -> list[Mapping[str, Any]]:
    if not isinstance(response, Mapping):
        raise ValueError("response must be a JSON object")
    _unexpected_fields(response, RESPONSE_FIELDS, "response")
    schema_version = response["schema_version"]
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, (int, float))
        or schema_version != SCHEMA_VERSION
    ):
        raise ValueError("response schema_version is unsupported")
    if response["audit_packet_sha256"] != audit_packet_sha256:
        raise ValueError("response audit_packet_sha256 does not match source")
    if not isinstance(response["reviews"], list):
        raise ValueError("response reviews must be a list")
    return response["reviews"]


def _validate_response(
    response: Mapping[str, Any], audit_packet: Mapping[str, Any], *, audit_packet_sha256: str, complete: bool
) -> None:
    reviews = _validate_response_header(response, audit_packet_sha256)
    candidates = _packet_candidates(audit_packet)
    response_ids = [item.get("defect_id") if isinstance(item, Mapping) else None for item in reviews]
    expected_ids = [candidate["defect_id"] for candidate in candidates]
    if response_ids != expected_ids:
        raise ValueError("response IDs must exactly match packet order, set, and count")
    for review, candidate in zip(reviews, candidates, strict=True):
        validate_review(review, candidate, complete=complete)


def validate_draft_response(response: Mapping[str, Any], audit_packet: Mapping[str, Any], *, audit_packet_sha256: str) -> None:
    _validate_response(response, audit_packet, audit_packet_sha256=audit_packet_sha256, complete=False)


def validate_complete_response(response: Mapping[str, Any], audit_packet: Mapping[str, Any], *, audit_packet_sha256: str) -> None:
    _validate_response(response, audit_packet, audit_packet_sha256=audit_packet_sha256, complete=True)


def validate_pinned_draft_response(response: Mapping[str, Any]) -> None:
    """Validate a draft only against the byte-pinned public audit packet."""
    validate_draft_response(
        response,
        load_pinned_audit_packet(),
        audit_packet_sha256=AUDIT_PACKET_SHA256,
    )


def validate_pinned_complete_response(response: Mapping[str, Any]) -> None:
    """Validate a complete response only against the byte-pinned public packet."""
    validate_complete_response(
        response,
        load_pinned_audit_packet(),
        audit_packet_sha256=AUDIT_PACKET_SHA256,
    )


def build_manifest(
    response_path: str | Path,
    *,
    created_at: str,
) -> dict[str, Any]:
    """Build a manifest from a complete response bound to the pinned packet."""
    _validate_timestamp(created_at, "manifest")
    response_path = ensure_external_output_path(response_path)
    try:
        response_bytes = response_path.read_bytes()
        response = json.loads(response_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("manifest response path must contain JSON") from exc
    if not isinstance(response, dict):
        raise ValueError("manifest response path must contain a JSON object")
    validate_pinned_complete_response(response)
    return {
        "schema_version": SCHEMA_VERSION,
        "response_filename": response_path.name,
        "response_sha256": hashlib.sha256(response_bytes).hexdigest(),
        "source_audit_packet_sha256": AUDIT_PACKET_SHA256,
        "response_count": len(response["reviews"]),
        "created_at": created_at,
    }


def write_manifest(
    path: str | Path,
    response_path: str | Path,
    *,
    created_at: str,
) -> dict[str, Any]:
    """Build and atomically create a manifest for a complete pinned response."""
    target = ensure_external_output_path(path)
    manifest = build_manifest(response_path, created_at=created_at)
    _atomic_create_json(target, manifest)
    return manifest


def _atomic_create_json(path: str | Path, value: Mapping[str, Any]) -> Path:
    """Create JSON once; an existing response is never silently replaced."""
    target = ensure_external_output_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        # link is atomic and fails if another writer has created the target.
        os.link(temporary, target)
    except FileExistsError as exc:
        raise ValueError(f"refusing to overwrite existing output: {target}") from exc
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def _cli_init(path: str, stdout: Any) -> None:
    target = _atomic_create_json(path, initialize_pinned_empty_response())
    print(f"Initialized empty audit draft: {target}", file=stdout)


def _read_response(path: str | Path) -> tuple[Path, dict[str, Any]]:
    target = ensure_external_output_path(path)
    return target, read_json(target)


def _validate_partial_pinned(response: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], int]:
    packet = load_pinned_audit_packet()
    reviews = _validate_response_header(response, AUDIT_PACKET_SHA256)
    candidates = _packet_candidates(packet)
    if [item.get("defect_id") if isinstance(item, Mapping) else None for item in reviews] != [item["defect_id"] for item in candidates]:
        raise ValueError("response IDs must exactly match packet order, set, and count")
    completed = 0
    for review, candidate in zip(reviews, candidates, strict=True):
        try:
            validate_review(review, candidate, complete=False)
        except ValueError:
            validate_review(review, candidate, complete=True)
            completed += 1
    return candidates, completed


def _atomic_replace_json(path: Path, value: Mapping[str, Any]) -> None:
    target = ensure_external_output_path(path)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def _show_json(label: str, value: Any, stdout: Any) -> None:
    print(f"{label}:", file=stdout)
    print(json.dumps(value, indent=2, sort_keys=True), file=stdout)


def _show_evidence(candidate: Mapping[str, Any], stdout: Any) -> None:
    material = candidate.get("candidate", {})
    if not isinstance(material, Mapping):
        raise ValueError(f"{candidate['defect_id']} candidate material must be an object")
    _show_json("Metadata", material.get("metadata", {}), stdout)
    _show_json("Commit", material.get("commit", material.get("commit_sha", "")), stdout)
    _show_json("Changed files", material.get("changed_files", []), stdout)
    _show_json("Candidate test files", material.get("candidate_test_files", material.get("test_files", [])), stdout)
    _show_json("Public evidence", material.get("evidence", []), stdout)
    print("Full patch:", file=stdout)
    print(material.get("patch", material.get("patch_text", "")), file=stdout)


def _answer(stdin: Any, stdout: Any, prompt: str) -> str:
    print(prompt, file=stdout)
    value = stdin.readline()
    if value == "":
        raise ValueError("review paused before a record was saved")
    return value.rstrip("\r\n")


def _record_from_reviewer(candidate: Mapping[str, Any], stdin: Any, stdout: Any) -> dict[str, Any]:
    reviewer_id = _answer(stdin, stdout, "reviewer_id:")
    reviewed_at = _answer(stdin, stdout, "reviewed_at (ISO-8601 with timezone):")
    evidence = _answer(stdin, stdout, "evidence_considered (comma-separated evidence IDs):")
    disposition = _answer(stdin, stdout, "disposition:")
    final_decision = _answer(stdin, stdout, "final_decision:")
    exclusion_reason = _answer(stdin, stdout, "final_exclusion_reason (empty if none):")
    boundaries = _answer(stdin, stdout, "final_boundaries (comma-separated, empty if none):")
    return {
        "defect_id": candidate["defect_id"], "selection_reasons": list(candidate["selection_reasons"]),
        "reviewer_id": reviewer_id,
        "reviewed_at": reviewed_at,
        "evidence_considered": [item.strip() for item in evidence.split(",") if item.strip()],
        "disposition": disposition,
        "final_decision": final_decision,
        "final_exclusion_reason": exclusion_reason,
        "final_boundaries": [item.strip() for item in boundaries.split(",") if item.strip()],
        "final_trigger": _answer(stdin, stdout, "final_trigger:"),
        "final_symptom": _answer(stdin, stdout, "final_symptom:"),
        "final_root_cause": _answer(stdin, stdout, "final_root_cause:"),
        "final_impact": _answer(stdin, stdout, "final_impact:"),
        "final_rationale": _answer(stdin, stdout, "final_rationale:"),
    }


def _cli_review(path: str, selected: str | None, stdin: Any, stdout: Any) -> None:
    target, response = _read_response(path)
    candidates, _ = _validate_partial_pinned(response)
    by_id = {item["defect_id"]: (index, item) for index, item in enumerate(candidates)}
    if selected is not None:
        if selected not in by_id:
            raise ValueError(f"unknown defect ID: {selected}")
        index, candidate = by_id[selected]
    else:
        pending = [(index, item) for index, item in enumerate(candidates) if response["reviews"][index] == _empty_review(item)]
        if not pending:
            raise ValueError("no pending reviews")
        index, candidate = pending[0]
    if response["reviews"][index] != _empty_review(candidate):
        raise ValueError(f"{candidate['defect_id']} is already completed")
    _show_evidence(candidate, stdout)
    if _answer(stdin, stdout, "Type ack to view immutable annotations:") != "ack":
        raise ValueError("review paused before annotations and no changes were saved")
    _show_json("A1/A2/A3 annotations", candidate.get("annotations", {}), stdout)
    _show_json("Selection reasons", candidate["selection_reasons"], stdout)
    record = _record_from_reviewer(candidate, stdin, stdout)
    validate_review(record, candidate, complete=True)
    response["reviews"][index] = record
    _atomic_replace_json(target, response)
    print(f"Saved review for {candidate['defect_id']}", file=stdout)


def _cli_status(path: str, stdout: Any) -> None:
    _, response = _read_response(path)
    _, completed = _validate_partial_pinned(response)
    disputed = sum(1 for review in response["reviews"] if review.get("disposition") == "disputed")
    print(f"{completed} completed, {len(response['reviews']) - completed} pending, {disputed} disputed", file=stdout)


def _cli_validate(path: str, stdout: Any) -> int:
    _, response = _read_response(path)
    _, completed = _validate_partial_pinned(response)
    if completed != len(response["reviews"]):
        print(f"Response is structurally valid but incomplete: {completed}/{len(response['reviews'])} completed", file=stdout)
        return 1
    validate_pinned_complete_response(response)
    print("Response is complete and valid", file=stdout)
    return 0


def _cli_freeze(response_path: str, manifest_path: str, stdout: Any) -> None:
    target, response = _read_response(response_path)
    validate_pinned_complete_response(response)
    write_manifest(manifest_path, target, created_at=datetime.now().astimezone().isoformat())
    print(f"Created external manifest: {ensure_external_output_path(manifest_path)}", file=stdout)


def main(
    argv: list[str] | None = None,
    stdin: Any = None,
    stdout: Any = None,
    stderr: Any = None,
) -> int:
    """Run the public human-audit command-line interface."""
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    parser = argparse.ArgumentParser(prog="human_audit", add_help=False, exit_on_error=False)
    subparsers = parser.add_subparsers(dest="command")
    init = subparsers.add_parser("init")
    init.add_argument("response_path")
    review = subparsers.add_parser("review")
    review.add_argument("response_path")
    review.add_argument("defect_id", nargs="?")
    status = subparsers.add_parser("status")
    status.add_argument("response_path")
    validate = subparsers.add_parser("validate")
    validate.add_argument("response_path")
    freeze = subparsers.add_parser("freeze")
    freeze.add_argument("response_path")
    freeze.add_argument("manifest_path")
    try:
        args = parser.parse_args(argv)
    except (argparse.ArgumentError, SystemExit):
        parser.print_usage(stderr)
        return 2
    try:
        if args.command == "init":
            _cli_init(args.response_path, stdout)
            return 0
        if args.command == "review":
            _cli_review(args.response_path, args.defect_id, stdin, stdout)
            return 0
        if args.command == "status":
            _cli_status(args.response_path, stdout)
            return 0
        if args.command == "validate":
            return _cli_validate(args.response_path, stdout)
        if args.command == "freeze":
            _cli_freeze(args.response_path, args.manifest_path, stdout)
            return 0
        parser.print_usage(stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
