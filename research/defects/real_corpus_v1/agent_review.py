"""Analyze three independent, provenance-bound Pass A agent annotations."""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from itertools import combinations
from typing import Any, Mapping, Sequence

from nanoharness.testing import DefectCodingSubmission


AGENT_IDS = ("A1", "A2", "A3")
DECISIONS = ("include", "exclude", "uncertain")
AUDIT_NAMESPACE = "agent-defect-human-audit-v1"
AUDIT_FRACTION = 0.1
FORBIDDEN_PACKET_KEYS = {
    "machine_precode",
    "operator_catalog",
    "partition",
    "private",
    "selection_rank",
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
            if normalized in FORBIDDEN_PACKET_KEYS or "private" in normalized:
                raise ValueError(f"forbidden blind packet material: {key}")
            _reject_forbidden_material(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_forbidden_material(nested)


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
        entry_ids = {entry.defect_id for entry in submission.entries}
        if entry_ids != packet_ids:
            raise ValueError("submission IDs must exactly match packet IDs")
        validated[agent_id] = submission

    shared_fields = (
        ("manual version", lambda item: item.manual_version),
        ("protocol", lambda item: item.agent_provenance.protocol_id),
        ("model", lambda item: item.agent_provenance.model_id),
        ("prompt", lambda item: item.agent_provenance.prompt_sha256),
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


def analyze_agent_reviews(
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

    common_include = [
        defect_id
        for defect_id, row in zip(defect_ids, decision_rows)
        if all(decision == "include" for decision in row)
    ]
    pairwise_boundary = {
        label: (
            sum(
                _jaccard(
                    entries[left][defect_id].boundaries,
                    entries[right][defect_id].boundaries,
                )
                for defect_id in common_include
            ) / len(common_include)
            if common_include else None
        )
        for label, (left, right) in zip(pair_labels, pairs)
    }
    boundary_values = [value for value in pairwise_boundary.values() if value is not None]
    counts = {
        "unanimous": sum(len(set(row)) == 1 for row in decision_rows),
        "two_to_one": sum(len(set(row)) == 2 for row in decision_rows),
        "three_way": sum(len(set(row)) == 3 for row in decision_rows),
        "any_uncertain": sum("uncertain" in row for row in decision_rows),
    }
    reasons = _selection_reasons(defect_ids, entries)
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
        "decision_agreement": {
            "fleiss_kappa": _fleiss_kappa(decision_rows),
            "pairwise_cohen_kappa": pairwise_kappa,
            "counts": counts,
        },
        "boundary_agreement": {
            "common_include_denominator": len(common_include),
            "pairwise_mean_jaccard": pairwise_boundary,
            "mean_pairwise_jaccard": (
                sum(boundary_values) / len(boundary_values) if boundary_values else None
            ),
        },
        "human_audit_selection": {
            "namespace": AUDIT_NAMESPACE,
            "fraction": AUDIT_FRACTION,
            "selected_count": len(reasons),
            "reasons_by_defect_id": reasons,
        },
    }


def build_review_artifacts(
    packet_bytes: bytes,
    packet: Mapping[str, Any],
    submissions: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the agreement report and selected, partition-blind human packet."""

    agreement = analyze_agent_reviews(packet_bytes, packet, submissions)
    candidate_by_id, validated = _validate_inputs(packet_bytes, packet, submissions)
    entries = _entry_maps(validated)
    reasons = agreement["human_audit_selection"]["reasons_by_defect_id"]
    candidates = []
    for defect_id in sorted(reasons):
        candidates.append({
            "defect_id": defect_id,
            "candidate": candidate_by_id[defect_id],
            "annotations": {
                agent_id: entries[agent_id][defect_id].model_dump(mode="json")
                for agent_id in AGENT_IDS
            },
            "selection_reasons": reasons[defect_id],
            "human_review": {
                "decision": None,
                "boundaries": [],
                "rationale": "",
            },
        })
    human_packet = {
        "schema_version": 1,
        "packet_sha256": agreement["packet_sha256"],
        "candidates": candidates,
    }
    return agreement, human_packet
