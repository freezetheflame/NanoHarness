import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

import nanoharness.testing as nh_testing
from nanoharness.testing import (
    AnnotationDecision,
    CoderCompletionDeclaration,
    CodingPass,
    DefectBoundary,
    DefectCodingEntry,
    DefectCodingSubmission,
)
from nanoharness.testing.defect_coding_cli import main as coding_cli_main


PACKET_DIGEST = "a" * 64


def _entry(defect_id="LG-1", **changes):
    values = {
        "defect_id": defect_id,
        "decision": AnnotationDecision.INCLUDE,
        "boundaries": [DefectBoundary.TOOL],
        "evidence_ids": ["LG-1-issue", "LG-1-fix"],
        "rationale": "The fixing change corrects a tool-boundary failure.",
    }
    values.update(changes)
    return DefectCodingEntry(**values)


def _completion(**changes):
    values = {
        "completed_at": datetime.now(timezone.utc),
        "independent": True,
        "packet_sha256": PACKET_DIGEST,
    }
    values.update(changes)
    return CoderCompletionDeclaration(**values)


def _agent_provenance(**changes):
    values = {
        "protocol_id": "agent-review-v1",
        "annotator_id": "A1",
        "model_id": "gpt-5",
        "prompt_sha256": "b" * 64,
        "input_sha256": PACKET_DIGEST,
        "artifact_revision": "7a76952",
        "started_at": datetime.now(timezone.utc),
    }
    values.update(changes)
    return nh_testing.AgentAnnotationProvenance(**values)


def test_pass_a_rejects_operator_labels():
    with pytest.raises(ValidationError, match="Pass A cannot contain operator"):
        DefectCodingSubmission(
            corpus_id="agent-defects-v1",
            pass_id=CodingPass.PASS_A,
            coder_id="H1",
            manual_version="1.0",
            packet_sha256=PACKET_DIGEST,
            entries=[_entry(operator_ids=["tool_result_stale"])],
        )


def test_pass_a_requires_decision_for_every_entry():
    with pytest.raises(ValidationError, match="Pass A requires a decision"):
        DefectCodingSubmission(
            corpus_id="agent-defects-v1",
            pass_id=CodingPass.PASS_A,
            coder_id="H1",
            manual_version="1.0",
            packet_sha256=PACKET_DIGEST,
            entries=[_entry(decision=None)],
        )


def test_pass_b_cannot_revise_pass_a_decisions():
    with pytest.raises(ValidationError, match="Pass B cannot revise"):
        DefectCodingSubmission(
            corpus_id="agent-defects-v1",
            pass_id=CodingPass.PASS_B,
            coder_id="H1",
            manual_version="1.0",
            packet_sha256=PACKET_DIGEST,
            entries=[_entry(operator_ids=["tool_result_stale"])],
        )


def test_submission_requires_unique_entry_ids():
    with pytest.raises(ValidationError, match="entry IDs must be unique"):
        DefectCodingSubmission(
            corpus_id="agent-defects-v1",
            pass_id=CodingPass.PASS_A,
            coder_id="H1",
            manual_version="1.0",
            packet_sha256=PACKET_DIGEST,
            entries=[_entry(), _entry()],
        )


def test_completion_requires_timezone_independence_and_matching_digest():
    with pytest.raises(ValidationError, match="timezone offset"):
        _completion(completed_at=datetime.now())
    with pytest.raises(ValidationError, match="confirm independence"):
        _completion(independent=False)
    with pytest.raises(ValidationError, match="completion packet digest"):
        DefectCodingSubmission(
            corpus_id="agent-defects-v1",
            pass_id=CodingPass.PASS_A,
            coder_id="H1",
            manual_version="1.0",
            packet_sha256=PACKET_DIGEST,
            entries=[_entry()],
            completion=_completion(packet_sha256="b" * 64),
        )


def test_valid_pass_a_round_trips():
    submission = DefectCodingSubmission(
        corpus_id="agent-defects-v1",
        pass_id=CodingPass.PASS_A,
        coder_id="H1",
        manual_version="1.0",
        packet_sha256=PACKET_DIGEST,
        entries=[_entry()],
        completion=_completion(),
    )

    restored = DefectCodingSubmission.model_validate_json(
        submission.model_dump_json()
    )

    assert restored == submission


def test_agent_provenance_round_trips_with_submission():
    submission = DefectCodingSubmission(
        corpus_id="agent-defects-v1",
        pass_id=CodingPass.PASS_A,
        coder_id="A1",
        manual_version="1.0",
        packet_sha256=PACKET_DIGEST,
        entries=[_entry()],
        agent_provenance=_agent_provenance(),
    )

    restored = DefectCodingSubmission.model_validate_json(
        submission.model_dump_json()
    )

    assert restored == submission
    assert restored.agent_provenance.protocol_id == "agent-review-v1"


@pytest.mark.parametrize(
    ("changes", "field_name"),
    [
        ({"protocol_id": ""}, "protocol_id"),
        ({"protocol_id": " \t"}, "protocol_id"),
        ({"annotator_id": "H1"}, "annotator_id"),
        ({"model_id": ""}, "model_id"),
        ({"model_id": " \t"}, "model_id"),
        ({"prompt_sha256": "A" * 64}, "prompt_sha256"),
        ({"input_sha256": "a" * 63}, "input_sha256"),
        ({"artifact_revision": "abcdef"}, "artifact_revision"),
        ({"artifact_revision": "       "}, "artifact_revision"),
        ({"artifact_revision": "revision!"}, "artifact_revision"),
        ({"artifact_revision": "a" * 41}, "artifact_revision"),
        ({"started_at": datetime.now()}, "started_at"),
    ],
)
def test_agent_provenance_rejects_invalid_fields(changes, field_name):
    with pytest.raises(ValidationError, match=field_name):
        _agent_provenance(**changes)


@pytest.mark.parametrize(
    ("provenance_changes", "message"),
    [
        ({"annotator_id": "A2"}, "annotator ID must match coder ID"),
        ({"input_sha256": "c" * 64}, "input digest must match packet digest"),
    ],
)
def test_submission_rejects_mismatched_agent_provenance(
    provenance_changes,
    message,
):
    with pytest.raises(ValidationError, match=message):
        DefectCodingSubmission(
            corpus_id="agent-defects-v1",
            pass_id=CodingPass.PASS_A,
            coder_id="A1",
            manual_version="1.0",
            packet_sha256=PACKET_DIGEST,
            entries=[_entry()],
            agent_provenance=_agent_provenance(**provenance_changes),
        )


def test_coding_cli_accepts_complete_digest_bound_submission(tmp_path, capsys):
    packet = tmp_path / "evidence_packet.json"
    packet.write_text(
        json.dumps({"candidates": [{"defect_id": "LG-1"}]}) + "\n",
        encoding="utf-8",
    )
    digest = __import__("hashlib").sha256(packet.read_bytes()).hexdigest()
    submission = DefectCodingSubmission(
        corpus_id="agent-defects-v1",
        pass_id=CodingPass.PASS_A,
        coder_id="H1",
        manual_version="1.0",
        packet_sha256=digest,
        entries=[_entry()],
        completion=_completion(packet_sha256=digest),
    )
    submission_path = tmp_path / "pass_a.json"
    submission_path.write_text(submission.model_dump_json(), encoding="utf-8")

    exit_code = coding_cli_main([
        str(packet),
        str(submission_path),
        "--require-complete",
    ])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output == {"errors": [], "valid": True}


def test_coding_cli_rejects_missing_candidate_and_wrong_digest(tmp_path, capsys):
    packet = tmp_path / "evidence_packet.json"
    packet.write_text(
        json.dumps({
            "candidates": [
                {"defect_id": "LG-1"},
                {"defect_id": "LG-2"},
            ]
        }) + "\n",
        encoding="utf-8",
    )
    submission = DefectCodingSubmission(
        corpus_id="agent-defects-v1",
        pass_id=CodingPass.PASS_A,
        coder_id="H1",
        manual_version="1.0",
        packet_sha256=PACKET_DIGEST,
        entries=[_entry()],
    )
    submission_path = tmp_path / "pass_a.json"
    submission_path.write_text(submission.model_dump_json(), encoding="utf-8")

    exit_code = coding_cli_main([
        str(packet),
        str(submission_path),
        "--require-complete",
    ])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert output["valid"] is False
    assert output["errors"] == [
        "packet digest mismatch",
        "submission IDs do not exactly match packet IDs",
        "completion declaration is required",
    ]


def test_coding_cli_requires_agent_provenance_when_requested(tmp_path, capsys):
    packet = tmp_path / "evidence_packet.json"
    packet.write_text(
        json.dumps({"candidates": [{"defect_id": "LG-1"}]}) + "\n",
        encoding="utf-8",
    )
    digest = __import__("hashlib").sha256(packet.read_bytes()).hexdigest()
    submission = DefectCodingSubmission(
        corpus_id="agent-defects-v1",
        pass_id=CodingPass.PASS_A,
        coder_id="A1",
        manual_version="1.0",
        packet_sha256=digest,
        entries=[_entry()],
    )
    submission_path = tmp_path / "pass_a.json"
    submission_path.write_text(submission.model_dump_json(), encoding="utf-8")

    exit_code = coding_cli_main([
        str(packet),
        str(submission_path),
        "--require-agent-provenance",
    ])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert output["errors"] == ["agent provenance is required"]


def test_coding_cli_rejects_wrong_agent_protocol(tmp_path, capsys):
    packet = tmp_path / "evidence_packet.json"
    packet.write_text(
        json.dumps({"candidates": [{"defect_id": "LG-1"}]}) + "\n",
        encoding="utf-8",
    )
    digest = __import__("hashlib").sha256(packet.read_bytes()).hexdigest()
    submission = DefectCodingSubmission(
        corpus_id="agent-defects-v1",
        pass_id=CodingPass.PASS_A,
        coder_id="A1",
        manual_version="1.0",
        packet_sha256=digest,
        entries=[_entry()],
        agent_provenance=_agent_provenance(
            protocol_id="agent-review-v2",
            input_sha256=digest,
        ),
    )
    submission_path = tmp_path / "pass_a.json"
    submission_path.write_text(submission.model_dump_json(), encoding="utf-8")

    exit_code = coding_cli_main([
        str(packet),
        str(submission_path),
        "--require-agent-provenance",
    ])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert output["errors"] == [
        "agent provenance protocol must be agent-review-v1"
    ]


def test_coding_cli_accepts_required_agent_provenance(tmp_path, capsys):
    packet = tmp_path / "evidence_packet.json"
    packet.write_text(
        json.dumps({"candidates": [{"defect_id": "LG-1"}]}) + "\n",
        encoding="utf-8",
    )
    digest = __import__("hashlib").sha256(packet.read_bytes()).hexdigest()
    submission = DefectCodingSubmission(
        corpus_id="agent-defects-v1",
        pass_id=CodingPass.PASS_A,
        coder_id="A1",
        manual_version="1.0",
        packet_sha256=digest,
        entries=[_entry()],
        agent_provenance=_agent_provenance(input_sha256=digest),
    )
    submission_path = tmp_path / "pass_a.json"
    submission_path.write_text(submission.model_dump_json(), encoding="utf-8")

    exit_code = coding_cli_main([
        str(packet),
        str(submission_path),
        "--require-agent-provenance",
    ])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output == {"errors": [], "valid": True}
