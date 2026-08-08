from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from nanoharness.testing import (
    AnnotationDecision,
    CoderCompletionDeclaration,
    CodingPass,
    DefectBoundary,
    DefectCodingEntry,
    DefectCodingSubmission,
)


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
