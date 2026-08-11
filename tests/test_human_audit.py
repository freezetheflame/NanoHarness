import copy
import hashlib
import io
import json
from datetime import datetime, timezone

import pytest
from jsonschema import Draft202012Validator

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


def test_cli_init_creates_pinned_empty_external_draft_atomically(tmp_path):
    output = tmp_path / "response.json"
    stdout, stderr = io.StringIO(), io.StringIO()

    result = human_audit.main(["init", str(output)], io.StringIO(), stdout, stderr)

    assert result == 0
    response = json.loads(output.read_text(encoding="utf-8"))
    validate_pinned_draft_response(response)
    assert "initialized" in stdout.getvalue().lower()
    assert stderr.getvalue() == ""


def test_cli_review_shows_public_evidence_before_annotations_and_saves_one_record(tmp_path, monkeypatch):
    packet = {
        "candidates": [{
            "defect_id": "D-1", "selection_reasons": ["decision_disagreement"],
            "candidate": {
                "metadata": {"repository": "example"}, "commit": "abc123",
                "changed_files": ["public.py"], "candidate_test_files": ["test_public.py"],
                "evidence": [{"evidence_id": "D-1-fix", "text": "public proof"}],
                "patch": "diff --git a/public.py b/public.py\n+",
            },
            "annotations": {"A1": "immutable annotation"},
        }]
    }
    monkeypatch.setattr(human_audit, "load_pinned_audit_packet", lambda: packet)
    output = tmp_path / "response.json"
    assert human_audit.main(["init", str(output)], io.StringIO(), io.StringIO(), io.StringIO()) == 0
    answers = "\n".join([
        "ack", "reviewer-1", "2026-08-11T12:00:00+08:00", "D-1-fix", "confirm", "include", "",
        "tool", "trigger", "symptom", "cause", "impact", "rationale",
    ]) + "\n"
    stdout, stderr = io.StringIO(), io.StringIO()

    result = human_audit.main(["review", str(output)], io.StringIO(answers), stdout, stderr)

    assert result == 0, stderr.getvalue()
    text = stdout.getvalue()
    assert text.index("Metadata") < text.index("Full patch") < text.index("ack") < text.index("A1")
    assert "immutable annotation" in text
    assert "majority" not in text.lower() and "default" not in text.lower()
    response = json.loads(output.read_text(encoding="utf-8"))
    assert response["reviews"][0]["reviewer_id"] == "reviewer-1"


def test_cli_review_bad_input_leaves_draft_unchanged_and_completed_record_is_not_overwritten(tmp_path, monkeypatch):
    packet = _packet()
    monkeypatch.setattr(human_audit, "load_pinned_audit_packet", lambda: packet)
    output = tmp_path / "response.json"
    assert human_audit.main(["init", str(output)], io.StringIO(), io.StringIO(), io.StringIO()) == 0
    before = output.read_bytes()
    result = human_audit.main(["review", str(output)], io.StringIO("no\n"), io.StringIO(), io.StringIO())
    assert result == 1
    assert output.read_bytes() == before

    response = json.loads(before)
    _complete_review(response["reviews"][0])
    output.write_text(json.dumps(response), encoding="utf-8")
    saved = output.read_bytes()
    result = human_audit.main(["review", str(output), "D-1"], io.StringIO(), io.StringIO(), io.StringIO())
    assert result == 1
    assert output.read_bytes() == saved


def test_cli_status_validate_and_freeze_obey_completion_and_sealed_output_rules(tmp_path, monkeypatch):
    packet = _packet()
    monkeypatch.setattr(human_audit, "load_pinned_audit_packet", lambda: packet)
    output = tmp_path / "response.json"
    assert human_audit.main(["init", str(output)], io.StringIO(), io.StringIO(), io.StringIO()) == 0
    stdout, stderr = io.StringIO(), io.StringIO()
    assert human_audit.main(["status", str(output)], io.StringIO(), stdout, stderr) == 0
    assert "0 completed" in stdout.getvalue() and "2 pending" in stdout.getvalue()
    assert human_audit.main(["validate", str(output)], io.StringIO(), io.StringIO(), io.StringIO()) == 1
    assert human_audit.main(["freeze", str(output), str(tmp_path / "manifest.json")], io.StringIO(), io.StringIO(), io.StringIO()) == 1
    response = json.loads(output.read_text(encoding="utf-8"))
    for review in response["reviews"]:
        _complete_review(review)
    output.write_text(json.dumps(response), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    assert human_audit.main(["freeze", str(output), str(manifest)], io.StringIO(), io.StringIO(), io.StringIO()) == 0
    assert manifest.exists()
    sealed = human_audit.DEFAULT_AUDIT_PACKET_PATH.parent / "forbidden.json"
    assert human_audit.main(["init", str(sealed)], io.StringIO(), io.StringIO(), io.StringIO()) == 1


def test_real_cli_dry_run_has_thirty_pending_and_never_overwrites_or_mutates_packet(tmp_path):
    source = human_audit.DEFAULT_AUDIT_PACKET_PATH
    before = source.read_bytes()
    output = tmp_path / "response.json"
    assert human_audit.main(["init", str(output)], io.StringIO(), io.StringIO(), io.StringIO()) == 0
    stdout = io.StringIO()
    assert human_audit.main(["status", str(output)], io.StringIO(), stdout, io.StringIO()) == 0
    assert "0 completed" in stdout.getvalue() and "30 pending" in stdout.getvalue()
    assert human_audit.main(["validate", str(output)], io.StringIO(), io.StringIO(), io.StringIO()) == 1
    original = output.read_bytes()
    assert human_audit.main(["init", str(output)], io.StringIO(), io.StringIO(), io.StringIO()) == 1
    assert output.read_bytes() == original
    response = json.loads(original)
    assert all(review["reviewer_id"] is None for review in response["reviews"])
    assert source.read_bytes() == before


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
    response = initialize_pinned_empty_response()
    response_path.write_text(json.dumps(response), encoding="utf-8")
    with pytest.raises(ValueError, match="reviewer_id"):
        build_manifest(response_path, created_at="2026-08-11T12:00:00+08:00")


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


@pytest.mark.parametrize(
    ("value", "accepted"),
    [(True, False), (False, False), (1, True), (1.0, True), (1.5, False), (2, False)],
)
def test_schema_version_matches_draft202012_numeric_semantics(value, accepted):
    response = initialize_empty_response(_packet(), audit_packet_sha256="a" * 64)
    response["schema_version"] = value
    schema_path = human_audit.Path(__file__).parents[1] / "research/defects/real_corpus_v1/human_audit_response.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["properties"]["schema_version"] == {"type": "integer", "const": 1}
    schema_accepts = not list(Draft202012Validator(schema).iter_errors(response))
    assert schema_accepts is accepted
    if accepted:
        validate_draft_response(response, _packet(), audit_packet_sha256="a" * 64)
    else:
        with pytest.raises(ValueError, match="schema_version"):
            validate_draft_response(response, _packet(), audit_packet_sha256="a" * 64)
    assert response["schema_version"] == value


def test_manifest_paths_must_be_external_to_sealed_formal_snapshot(tmp_path):
    source = human_audit.DEFAULT_AUDIT_PACKET_PATH
    before = source.read_bytes()
    response = initialize_pinned_empty_response()
    with pytest.raises(ValueError, match="external to the sealed formal snapshot"):
        build_manifest(source, created_at="2026-08-11T12:00:00+08:00")
    with pytest.raises(ValueError, match="external to the sealed formal snapshot"):
        write_manifest(
            source.parent / "forbidden.manifest.json",
            tmp_path / "response.json",
            created_at="2026-08-11T12:00:00+08:00",
        )
    assert source.read_bytes() == before


def test_manifest_reads_external_response_once_and_binds_its_exact_bytes(
    tmp_path, monkeypatch
):
    packet = _packet()
    response = initialize_empty_response(packet, audit_packet_sha256=AUDIT_PACKET_SHA256)
    for review in response["reviews"]:
        _complete_review(review)
    response_path = tmp_path / "human_audit_response.json"
    response_bytes = json.dumps(response, sort_keys=True).encode("utf-8")
    response_path.write_bytes(response_bytes)
    monkeypatch.setattr(human_audit, "load_pinned_audit_packet", lambda: packet)
    original_read = human_audit.Path.read_bytes
    reads = 0

    def counted_read(path):
        nonlocal reads
        if path == response_path:
            reads += 1
        return original_read(path)

    monkeypatch.setattr(human_audit.Path, "read_bytes", counted_read)
    manifest = build_manifest(response_path, created_at="2026-08-11T12:00:00+08:00")
    assert reads == 1
    assert manifest["response_sha256"] == hashlib.sha256(response_bytes).hexdigest()
    assert manifest["response_count"] == 2

    manifest_path = tmp_path / "human_audit_response.manifest.json"
    assert write_manifest(manifest_path, response_path, created_at="2026-08-11T12:00:00+08:00") == manifest
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == manifest


def test_manifest_publication_never_replaces_existing_file_or_leaves_temporary(tmp_path, monkeypatch):
    packet = _packet()
    response = initialize_empty_response(packet, audit_packet_sha256=AUDIT_PACKET_SHA256)
    for review in response["reviews"]:
        _complete_review(review)
    response_path = tmp_path / "response.json"
    response_path.write_text(json.dumps(response), encoding="utf-8")
    monkeypatch.setattr(human_audit, "load_pinned_audit_packet", lambda: packet)
    manifest_path = tmp_path / "manifest.json"

    write_manifest(manifest_path, response_path, created_at="2026-08-11T12:00:00+08:00")
    original = manifest_path.read_bytes()
    with pytest.raises(ValueError, match="refusing to overwrite"):
        write_manifest(manifest_path, response_path, created_at="2026-08-11T12:00:00+08:00")

    assert manifest_path.read_bytes() == original
    assert list(tmp_path.glob(".manifest.json.*.tmp")) == []
