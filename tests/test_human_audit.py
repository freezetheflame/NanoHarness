import copy
import hashlib
import json
from datetime import datetime, timezone

import pytest

import research.defects.real_corpus_v1.human_audit as human_audit
from research.defects.real_corpus_v1.human_audit import (
    AUDIT_PACKET_SHA256,
    build_manifest,
    initialize_pinned_empty_response,
    initialize_empty_response,
    load_pinned_audit_packet,
    sha256_file,
    validate_pinned_draft_response,
    validate_complete_response,
    validate_draft_response,
    validate_review,
    write_manifest,
)


def _packet():
    return {
        "candidates": [
            {
                "defect_id": "D-1",
                "selection_reasons": ["decision_disagreement"],
                "candidate": {"evidence": [{"evidence_id": "D-1-fix"}]},
            },
            {
                "defect_id": "D-2",
                "selection_reasons": ["boundary_disagreement"],
                "candidate": {"evidence": [{"evidence_id": "D-2-fix"}]},
            },
        ]
    }


def _complete_review(review, *, decision="include"):
    review.update(
        {
            "reviewer_id": "reviewer-1",
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "evidence_considered": [f"{review['defect_id']}-fix"],
            "disposition": "confirm",
            "final_decision": decision,
            "final_boundaries": ["tool"] if decision == "include" else [],
            "final_exclusion_reason": "" if decision == "include" else "not_agent_boundary",
            "final_trigger": "A tool is called.",
            "final_symptom": "The tool result is wrong.",
            "final_root_cause": "The result is mishandled.",
            "final_impact": "The agent can fail.",
            "final_rationale": "The frozen evidence supports this judgment.",
        }
    )
    return review


def test_initialize_empty_response_copies_packet_ids_order_and_selection_reasons():
    response = initialize_empty_response(_packet(), audit_packet_sha256="a" * 64)

    assert response["audit_packet_sha256"] == "a" * 64
    assert [review["defect_id"] for review in response["reviews"]] == ["D-1", "D-2"]
    assert response["reviews"][0]["selection_reasons"] == ["decision_disagreement"]
    validate_draft_response(response, _packet(), audit_packet_sha256="a" * 64)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda response: response.update(audit_packet_sha256="b" * 64), "audit_packet_sha256"),
        (lambda response: response["reviews"].reverse(), "IDs must exactly match packet order"),
        (lambda response: response["reviews"][0].update(selection_reasons=[]), "selection_reasons"),
        (lambda response: response["reviews"][0].update(extra="no"), "unexpected field"),
    ],
)
def test_draft_rejects_unbound_or_noncanonical_fields(change, message):
    response = initialize_empty_response(_packet(), audit_packet_sha256="a" * 64)
    change(response)

    with pytest.raises(ValueError, match=message):
        validate_draft_response(response, _packet(), audit_packet_sha256="a" * 64)


def test_complete_response_requires_human_fields_and_validates_review_invariants():
    response = initialize_empty_response(_packet(), audit_packet_sha256="a" * 64)
    _complete_review(response["reviews"][0])
    _complete_review(response["reviews"][1], decision="exclude")
    validate_complete_response(response, _packet(), audit_packet_sha256="a" * 64)

    invalid = copy.deepcopy(response["reviews"][0])
    invalid["evidence_considered"] = ["not-in-packet"]
    with pytest.raises(ValueError, match="D-1 evidence_considered"):
        validate_review(invalid, _packet()["candidates"][0], complete=True)

    invalid = copy.deepcopy(response["reviews"][0])
    invalid["reviewed_at"] = "2026-08-11T12:00:00"
    with pytest.raises(ValueError, match="D-1 reviewed_at must be timezone-aware"):
        validate_review(invalid, _packet()["candidates"][0], complete=True)

    invalid = copy.deepcopy(response["reviews"][0])
    invalid["final_boundaries"] = ["bad-boundary"]
    with pytest.raises(ValueError, match="D-1 final_boundaries"):
        validate_review(invalid, _packet()["candidates"][0], complete=True)

    invalid = copy.deepcopy(response["reviews"][0])
    invalid["final_exclusion_reason"] = "not_agent_boundary"
    with pytest.raises(ValueError, match="D-1 include requires empty final_exclusion_reason"):
        validate_review(invalid, _packet()["candidates"][0], complete=True)

    disputed = copy.deepcopy(response["reviews"][0])
    disputed.update(disposition="disputed", final_decision="uncertain", final_boundaries=[], final_exclusion_reason="")
    validate_review(disputed, _packet()["candidates"][0], complete=True)
    disputed["final_decision"] = "include"
    with pytest.raises(ValueError, match="D-1 disputed requires final_decision uncertain"):
        validate_review(disputed, _packet()["candidates"][0], complete=True)


def test_draft_permits_only_empty_judgments_and_complete_requires_all_reviews():
    response = initialize_empty_response(_packet(), audit_packet_sha256="a" * 64)
    validate_draft_response(response, _packet(), audit_packet_sha256="a" * 64)
    response["reviews"][0]["reviewer_id"] = "premature"
    with pytest.raises(ValueError, match="D-1 draft review must remain empty"):
        validate_draft_response(response, _packet(), audit_packet_sha256="a" * 64)
    response = initialize_empty_response(_packet(), audit_packet_sha256="a" * 64)
    with pytest.raises(ValueError, match="D-1 reviewer_id"):
        validate_complete_response(response, _packet(), audit_packet_sha256="a" * 64)


def test_manifest_is_external_non_self_referential_and_hash_bound(tmp_path):
    response_path = tmp_path / "human_audit_response.json"
    response_path.write_text('{"schema_version": 1}\n', encoding="utf-8")
    response = initialize_pinned_empty_response()
    with pytest.raises(ValueError, match="reviewer_id"):
        build_manifest(response_path, response, created_at="2026-08-11T12:00:00+08:00")


def test_real_packet_dry_run_does_not_mutate_source(tmp_path):
    source = (
        "research/defects/real_corpus_v1/formal/agent-review-v1/analysis/"
        "human_audit_packet.json"
    )
    before = sha256_file(source)
    assert before == AUDIT_PACKET_SHA256
    packet = load_pinned_audit_packet()
    response = initialize_pinned_empty_response()
    output = tmp_path.resolve() / "human_audit_response.json"
    output.write_text(json.dumps(response), encoding="utf-8")
    validate_pinned_draft_response(response)
    assert len(response["reviews"]) == 30
    assert all(review["reviewer_id"] is None for review in response["reviews"])
    assert sha256_file(source) == before


def test_pinned_api_rejects_self_consistent_different_packet_and_altered_bytes(
    tmp_path, monkeypatch
):
    different = initialize_empty_response(_packet(), audit_packet_sha256=AUDIT_PACKET_SHA256)
    with pytest.raises(ValueError, match="response IDs"):
        validate_pinned_draft_response(different)

    altered = tmp_path / "human_audit_packet.json"
    altered.write_bytes(human_audit.DEFAULT_AUDIT_PACKET_PATH.read_bytes() + b"\n")
    monkeypatch.setattr(human_audit, "DEFAULT_AUDIT_PACKET_PATH", altered)
    with pytest.raises(ValueError, match="pinned audit packet SHA-256"):
        load_pinned_audit_packet()
