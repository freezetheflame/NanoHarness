"""Analyze three independent, provenance-bound Pass A agent annotations."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from itertools import combinations
from typing import Any, Mapping, Sequence

from nanoharness.testing import DefectCodingSubmission


AGENT_IDS = ("A1", "A2", "A3")
DECISIONS = ("include", "exclude", "uncertain")
AUDIT_NAMESPACE = "agent-defect-human-audit-v1"
AUDIT_FRACTION = 0.1
FORBIDDEN_PACKET_KEY_FRAGMENTS = (
    "partition",
    "operator",
    "private",
    "machine",
    "selection",
)
PROVENANCE_FIELDS = {
    "protocol_id",
    "annotator_id",
    "model_id",
    "prompt_sha256",
    "input_sha256",
    "artifact_revision",
    "started_at",
}
COMPLETION_FIELDS = {"completed_at", "independent", "packet_sha256"}
SUBMISSION_FIELDS = {
    "schema_version",
    "corpus_id",
    "pass_id",
    "coder_id",
    "manual_version",
    "packet_sha256",
    "entries",
    "completion",
    "agent_provenance",
}
ENTRY_FIELDS = {
    "defect_id",
    "decision",
    "exclusion_reason",
    "boundaries",
    "operator_ids",
    "trigger",
    "symptom",
    "root_cause",
    "impact",
    "evidence_ids",
    "rationale",
}


def _unique_by_id(items: Sequence[Mapping[str, Any]], label: str) -> dict[str, Any]:
    by_id = {item["defect_id"]: item for item in items}
    if len(by_id) != len(items):
        raise ValueError(f"{label} defect IDs must be unique")
    return by_id


def _reject_forbidden_material(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).lower()
            if any(
                fragment in normalized
                for fragment in FORBIDDEN_PACKET_KEY_FRAGMENTS
            ):
                raise ValueError(f"forbidden blind packet material: {key}")
            _reject_forbidden_material(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_forbidden_material(nested)


def _parse_raw_inputs(
    packet_bytes: bytes,
    submission_bytes: Mapping[str, bytes],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    if set(submission_bytes) != set(AGENT_IDS) or len(submission_bytes) != len(
        AGENT_IDS
    ):
        raise ValueError("submissions must contain exactly A1, A2, A3")
    packet = json.loads(packet_bytes)
    if not isinstance(packet, dict):
        raise ValueError("packet must be a JSON object")
    submissions = {}
    for agent_id in AGENT_IDS:
        submission = json.loads(submission_bytes[agent_id])
        if not isinstance(submission, dict):
            raise ValueError(f"{agent_id} submission must be a JSON object")
        submissions[agent_id] = submission
    return packet, submissions


def _reject_unexpected_source_fields(
    agent_id: str,
    submission: Mapping[str, Any],
) -> None:
    extras = set(submission) - SUBMISSION_FIELDS
    if extras:
        raise ValueError(
            f"{agent_id} submission has unexpected field: {sorted(extras)[0]}"
        )
    entries = submission.get("entries")
    if isinstance(entries, list):
        for index, entry in enumerate(entries):
            if isinstance(entry, Mapping):
                extras = set(entry) - ENTRY_FIELDS
                if extras:
                    raise ValueError(
                        f"{agent_id} entry {index} has unexpected field: "
                        f"{sorted(extras)[0]}"
                    )
    for container, allowed in (
        ("agent_provenance", PROVENANCE_FIELDS),
        ("completion", COMPLETION_FIELDS),
    ):
        value = submission.get(container)
        if isinstance(value, Mapping):
            extras = set(value) - allowed
            if extras:
                raise ValueError(
                    f"{agent_id} {container} has unexpected field: "
                    f"{sorted(extras)[0]}"
                )


def validate_pass_a_semantics(
    submission: DefectCodingSubmission,
    candidate_by_id: Mapping[str, Mapping[str, Any]],
    *,
    expected_order: Sequence[str],
) -> None:
    """Validate one complete Pass A judgment against defect-local evidence."""

    if submission.pass_id.value != "pass_a":
        raise ValueError("submission must be Pass A")
    entry_ids = [entry.defect_id for entry in submission.entries]
    if entry_ids != list(expected_order):
        raise ValueError("submission IDs must exactly match packet order")
    for entry in submission.entries:
        if entry.operator_ids:
            raise ValueError(f"{entry.defect_id} Pass A operator_ids must be empty")
        if len(entry.boundaries) != len(set(entry.boundaries)):
            raise ValueError(f"{entry.defect_id} has duplicate boundaries")
        if len(entry.evidence_ids) != len(set(entry.evidence_ids)):
            raise ValueError(f"{entry.defect_id} has duplicate evidence_ids")
        candidate_evidence_ids = {
            evidence["evidence_id"]
            for evidence in candidate_by_id[entry.defect_id].get("evidence", [])
        }
        if (
            not entry.evidence_ids
            or not set(entry.evidence_ids) <= candidate_evidence_ids
        ):
            raise ValueError(f"{entry.defect_id} has invalid evidence IDs")
        if not entry.rationale.strip():
            raise ValueError(f"{entry.defect_id} requires rationale")
        decision = entry.decision.value
        if decision == "include":
            if entry.exclusion_reason.strip():
                raise ValueError(
                    f"{entry.defect_id} include requires empty exclusion_reason"
                )
            if not entry.boundaries:
                raise ValueError(f"{entry.defect_id} include requires boundaries")
            for field_name in ("trigger", "symptom", "root_cause", "impact"):
                if not getattr(entry, field_name).strip():
                    raise ValueError(
                        f"{entry.defect_id} include requires {field_name}"
                    )
        elif decision == "exclude":
            if not entry.exclusion_reason.strip():
                raise ValueError(
                    f"{entry.defect_id} exclude requires exclusion_reason"
                )
            if entry.boundaries:
                raise ValueError(
                    f"{entry.defect_id} exclude requires empty boundaries"
                )
        else:
            if entry.exclusion_reason.strip():
                raise ValueError(
                    f"{entry.defect_id} uncertain requires empty exclusion_reason"
                )
            if entry.boundaries:
                raise ValueError(
                    f"{entry.defect_id} uncertain requires empty boundaries"
                )


def _validate_inputs(
    packet_bytes: bytes,
    packet: Mapping[str, Any],
    submissions: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, DefectCodingSubmission]]:
    if set(submissions) != set(AGENT_IDS) or len(submissions) != len(AGENT_IDS):
        raise ValueError("submissions must contain exactly A1, A2, A3")
    candidates = packet.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("packet candidates must be a list")
    _reject_forbidden_material(candidates)
    candidate_by_id = _unique_by_id(candidates, "packet")
    packet_ids = set(candidate_by_id)
    packet_digest = hashlib.sha256(packet_bytes).hexdigest()

    validated: dict[str, DefectCodingSubmission] = {}
    for agent_id in AGENT_IDS:
        _reject_unexpected_source_fields(agent_id, submissions[agent_id])
        submission = DefectCodingSubmission.model_validate(submissions[agent_id])
        if submission.coder_id != agent_id:
            raise ValueError(f"{agent_id} submission coder ID mismatch")
        if submission.pass_id.value != "pass_a":
            raise ValueError("all submissions must be Pass A")
        if submission.packet_sha256 != packet_digest:
            raise ValueError("all submissions must match the packet digest")
        if submission.agent_provenance is None:
            raise ValueError("all submissions require agent provenance")
        if submission.agent_provenance.protocol_id != "agent-review-v1":
            raise ValueError("all submissions must use protocol agent-review-v1")
        if submission.completion is None:
            raise ValueError("all submissions require completion")
        validate_pass_a_semantics(
            submission,
            candidate_by_id,
            expected_order=[candidate["defect_id"] for candidate in candidates],
        )
        validated[agent_id] = submission

    shared_fields = (
        ("schema version", lambda item: item.schema_version),
        ("corpus", lambda item: item.corpus_id),
        ("manual version", lambda item: item.manual_version),
        ("protocol", lambda item: item.agent_provenance.protocol_id),
        ("model", lambda item: item.agent_provenance.model_id),
        ("prompt", lambda item: item.agent_provenance.prompt_sha256),
        (
            "artifact revision",
            lambda item: item.agent_provenance.artifact_revision,
        ),
    )
    for label, accessor in shared_fields:
        if len({accessor(item) for item in validated.values()}) != 1:
            raise ValueError(f"all submissions must use the same {label}")
    return candidate_by_id, validated


def _entry_maps(
    submissions: Mapping[str, DefectCodingSubmission],
) -> dict[str, dict[str, Any]]:
    return {
        agent_id: {entry.defect_id: entry for entry in submissions[agent_id].entries}
        for agent_id in AGENT_IDS
    }


def _cohen_kappa(left: Sequence[str], right: Sequence[str]) -> float | None:
    sample_size = len(left)
    if sample_size == 0:
        return None
    observed = sum(a == b for a, b in zip(left, right)) / sample_size
    left_counts = Counter(left)
    right_counts = Counter(right)
    expected = sum(
        left_counts[category] * right_counts[category] / sample_size**2
        for category in DECISIONS
    )
    return None if expected == 1.0 else (observed - expected) / (1.0 - expected)


def _fleiss_kappa(rows: Sequence[Sequence[str]]) -> float | None:
    if not rows:
        return None
    raters = len(AGENT_IDS)
    observed = sum(
        (sum(count**2 for count in Counter(row).values()) - raters)
        / (raters * (raters - 1))
        for row in rows
    ) / len(rows)
    totals = Counter(decision for row in rows for decision in row)
    expected = sum(
        (totals[category] / (len(rows) * raters)) ** 2
        for category in DECISIONS
    )
    return None if expected == 1.0 else (observed - expected) / (1.0 - expected)


def _jaccard(left: Sequence[Any], right: Sequence[Any]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set and not right_set:
        return 1.0
    return len(left_set & right_set) / len(left_set | right_set)


def _audit_rank(defect_id: str) -> str:
    return hashlib.sha256(f"{AUDIT_NAMESPACE}|{defect_id}".encode("utf-8")).hexdigest()


def _selection_reasons(
    defect_ids: Sequence[str], entry_maps: Mapping[str, Mapping[str, Any]]
) -> dict[str, list[str]]:
    reasons: dict[str, list[str]] = {}
    consistent = []
    for defect_id in defect_ids:
        entries = [entry_maps[agent_id][defect_id] for agent_id in AGENT_IDS]
        decisions = {entry.decision.value for entry in entries}
        boundaries = {tuple(sorted(boundary.value for boundary in entry.boundaries)) for entry in entries}
        item_reasons = []
        if len(decisions) != 1:
            item_reasons.append("decision_disagreement")
        if "uncertain" in decisions:
            item_reasons.append("any_uncertain")
        if len(boundaries) != 1:
            item_reasons.append("boundary_disagreement")
        if item_reasons:
            reasons[defect_id] = item_reasons
        else:
            consistent.append(defect_id)
    audit_count = math.ceil(len(consistent) * AUDIT_FRACTION)
    for defect_id in sorted(consistent, key=lambda item: (_audit_rank(item), item))[:audit_count]:
        reasons[defect_id] = ["deterministic_consistency_audit"]
    return {defect_id: reasons[defect_id] for defect_id in sorted(reasons)}


def _analyze_parsed_reviews(
    packet_bytes: bytes,
    packet: Mapping[str, Any],
    submissions: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate inputs and return deterministic agreement and audit selection."""

    candidate_by_id, validated = _validate_inputs(packet_bytes, packet, submissions)
    defect_ids = sorted(candidate_by_id)
    entries = _entry_maps(validated)
    decision_rows = [
        [entries[agent_id][defect_id].decision.value for agent_id in AGENT_IDS]
        for defect_id in defect_ids
    ]
    pairs = list(combinations(AGENT_IDS, 2))
    pair_labels = [f"{left}:{right}" for left, right in pairs]
    pairwise_kappa = {
        label: _cohen_kappa(
            [entries[left][defect_id].decision.value for defect_id in defect_ids],
            [entries[right][defect_id].decision.value for defect_id in defect_ids],
        )
        for label, (left, right) in zip(pair_labels, pairs)
    }

    pairwise_included = {
        label: [
            defect_id
            for defect_id in defect_ids
            if entries[left][defect_id].decision.value == "include"
            and entries[right][defect_id].decision.value == "include"
        ]
        for label, (left, right) in zip(pair_labels, pairs)
    }
    pairwise_boundary = {}
    for label, (left, right) in zip(pair_labels, pairs):
        eligible_ids = pairwise_included[label]
        pairwise_boundary[label] = (
            sum(
                _jaccard(
                    entries[left][defect_id].boundaries,
                    entries[right][defect_id].boundaries,
                )
                for defect_id in eligible_ids
            ) / len(eligible_ids)
            if eligible_ids else None
        )
    boundary_values = [value for value in pairwise_boundary.values() if value is not None]
    counts = {
        "unanimous": sum(len(set(row)) == 1 for row in decision_rows),
        "two_to_one": sum(len(set(row)) == 2 for row in decision_rows),
        "three_way": sum(len(set(row)) == 3 for row in decision_rows),
        "any_uncertain": sum("uncertain" in row for row in decision_rows),
    }
    reasons = _selection_reasons(defect_ids, entries)
    deterministic_audit_count = sum(
        item_reasons == ["deterministic_consistency_audit"]
        for item_reasons in reasons.values()
    )
    non_consistent_selection_count = len(reasons) - deterministic_audit_count
    first = validated[AGENT_IDS[0]]
    return {
        "schema_version": 1,
        "protocol_id": "agent-review-v1",
        "pass_id": "pass_a",
        "annotator_ids": list(AGENT_IDS),
        "packet_sha256": hashlib.sha256(packet_bytes).hexdigest(),
        "manual_version": first.manual_version,
        "model_id": first.agent_provenance.model_id,
        "prompt_sha256": first.agent_provenance.prompt_sha256,
        "sample_size": len(defect_ids),
        "missing_count": 0,
        "invalid_count": 0,
        "decision_marginals": {
            agent_id: {
                category: sum(
                    entries[agent_id][defect_id].decision.value == category
                    for defect_id in defect_ids
                )
                for category in DECISIONS
            }
            for agent_id in AGENT_IDS
        },
        "decision_agreement": {
            "fleiss_kappa": _fleiss_kappa(decision_rows),
            "fleiss_denominator": len(defect_ids),
            "pairwise_cohen_kappa": pairwise_kappa,
            "pairwise_cohen_denominators": {
                label: len(defect_ids) for label in pair_labels
            },
            "counts": counts,
        },
        "boundary_agreement": {
            "pairwise_denominators": {
                label: len(pairwise_included[label]) for label in pair_labels
            },
            "pairwise_mean_jaccard": pairwise_boundary,
            "mean_pairwise_jaccard": (
                sum(boundary_values) / len(boundary_values) if boundary_values else None
            ),
        },
        "human_audit_selection": {
            "namespace": AUDIT_NAMESPACE,
            "fraction": AUDIT_FRACTION,
            "selected_count": len(reasons),
            "eligible_unanimous_count": (
                len(defect_ids) - non_consistent_selection_count
            ),
            "deterministic_audit_count": deterministic_audit_count,
            "reasons_by_defect_id": reasons,
        },
    }


def analyze_agent_reviews(
    packet_bytes: bytes,
    submission_bytes: Mapping[str, bytes],
) -> dict[str, Any]:
    """Parse, validate, and analyze one immutable raw-input snapshot."""

    packet, submissions = _parse_raw_inputs(packet_bytes, submission_bytes)
    return _analyze_parsed_reviews(packet_bytes, packet, submissions)


def build_review_artifacts(
    packet_bytes: bytes,
    submission_bytes: Mapping[str, bytes],
    *,
    patch_payloads: Mapping[str, bytes] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the agreement report and selected, partition-blind human packet."""

    packet, submissions = _parse_raw_inputs(packet_bytes, submission_bytes)
    agreement = _analyze_parsed_reviews(packet_bytes, packet, submissions)
    candidate_by_id, validated = _validate_inputs(packet_bytes, packet, submissions)
    raw_submission_digests = {
        agent_id: hashlib.sha256(submission_bytes[agent_id]).hexdigest()
        for agent_id in AGENT_IDS
    }
    entries = _entry_maps(validated)
    reasons = agreement["human_audit_selection"]["reasons_by_defect_id"]
    if patch_payloads is None or not set(reasons) <= set(patch_payloads):
        raise ValueError("patch payloads are required for every selected candidate")
    candidates = []
    for defect_id in sorted(reasons):
        candidate = dict(candidate_by_id[defect_id])
        patch_path = candidate.get("patch_path")
        expected_patch_digest = candidate.get("patch_sha256")
        if not isinstance(patch_path, str) or not patch_path:
            raise ValueError(f"{defect_id} selected candidate requires patch_path")
        if (
            not isinstance(expected_patch_digest, str)
            or len(expected_patch_digest) != 64
            or any(
                character not in "0123456789abcdef"
                for character in expected_patch_digest
            )
        ):
            raise ValueError(f"{defect_id} selected candidate requires patch_sha256")
        patch_payload = patch_payloads[defect_id]
        actual_patch_digest = hashlib.sha256(patch_payload).hexdigest()
        if actual_patch_digest != expected_patch_digest:
            raise ValueError(f"{defect_id} patch SHA-256 mismatch")
        try:
            candidate["patch_text"] = patch_payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(f"{defect_id} patch must be UTF-8") from error
        annotations = {}
        for agent_id in AGENT_IDS:
            annotation = entries[agent_id][defect_id].model_dump(mode="json")
            if not annotation["operator_ids"]:
                annotation.pop("operator_ids")
            annotations[agent_id] = annotation
        candidates.append({
            "defect_id": defect_id,
            "candidate": candidate,
            "annotations": annotations,
            "selection_reasons": reasons[defect_id],
            "human_review": {
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
            },
        })
    human_packet = {
        "schema_version": 1,
        "packet_sha256": agreement["packet_sha256"],
        "source_submissions": {
            agent_id: {
                "sha256": raw_submission_digests[agent_id],
                "provenance": validated[agent_id].agent_provenance.model_dump(
                    mode="json"
                ),
                "completion": validated[agent_id].completion.model_dump(mode="json"),
            }
            for agent_id in AGENT_IDS
        },
        "candidates": candidates,
    }
    return agreement, human_packet
