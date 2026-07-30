import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from nanoharness.testing import (
    AnnotationDecision,
    CorpusPartition,
    DefectAnnotation,
    DefectBoundary,
    DefectCorpus,
    DefectCorpusAnalyzer,
    DefectEvidence,
    DefectRecord,
    DefectStatus,
    EvidenceKind,
)
from nanoharness.testing.defect_cli import main as defect_cli_main


def _evidence(defect_id):
    return [
        DefectEvidence(
            evidence_id=f"{defect_id}-issue",
            kind=EvidenceKind.ISSUE_REPORT,
            locator=f"https://example.test/issues/{defect_id}",
        ),
        DefectEvidence(
            evidence_id=f"{defect_id}-test",
            kind=EvidenceKind.REGRESSION_TEST,
            locator=f"tests/test_{defect_id}.py",
            test_id=f"test_{defect_id}",
        ),
    ]


def _annotation(annotator, decision, *, boundary=None, operator=None):
    return DefectAnnotation(
        annotator_id=annotator,
        decision=decision,
        boundaries=[boundary] if boundary else [],
        operator_ids=[operator] if operator else [],
        rationale="independent coding rationale",
    )


def _verified(
    defect_id,
    *,
    partition=CorpusPartition.DERIVATION,
    operator="evaluator_flip",
    annotations=None,
):
    return DefectRecord(
        defect_id=defect_id,
        title=f"Defect {defect_id}",
        source_repository="owner/runtime",
        summary="The runtime reports an incorrect task result.",
        status=DefectStatus.VERIFIED,
        partition=partition,
        boundaries=[DefectBoundary.EVALUATOR],
        symptoms=["incorrect success verdict"],
        trigger="The evaluator rejects a terminated run.",
        root_cause="Termination and goal achievement are conflated.",
        evidence=_evidence(defect_id),
        operator_ids=[operator] if operator else [],
        annotations=annotations or [],
        decision_rationale="Evidence confirms an implementation defect.",
    )


def test_candidate_corpus_round_trips_and_repository_seed_validates():
    path = Path(__file__).parents[1] / "research" / "defects" / "corpus.json"
    corpus = DefectCorpus.model_validate_json(path.read_text())

    restored = DefectCorpus.model_validate_json(corpus.model_dump_json())

    assert restored == corpus
    assert corpus.records[0].status is DefectStatus.CANDIDATE
    assert corpus.records[0].partition is CorpusPartition.DERIVATION


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"partition": None}, "corpus partition"),
        ({"boundaries": []}, "at least one boundary"),
        ({"trigger": ""}, "trigger description"),
        ({"root_cause": ""}, "root-cause description"),
        ({"decision_rationale": ""}, "decision rationale"),
        ({"evidence": _evidence("same")[:1]}, "two evidence kinds"),
    ],
)
def test_verified_defect_requires_auditable_fields(changes, message):
    data = _verified("verified").model_dump()
    data.update(changes)

    with pytest.raises(ValidationError, match=message):
        DefectRecord.model_validate(data)


def test_excluded_defect_requires_reason():
    with pytest.raises(ValidationError, match="exclusion_reason"):
        DefectRecord(
            defect_id="excluded",
            title="Not a defect",
            source_repository="owner/runtime",
            summary="A feature request was initially collected.",
            status=DefectStatus.EXCLUDED,
        )


def test_corpus_rejects_unknown_operator_references():
    record = _verified(
        "unknown",
        operator="missing",
        annotations=[_annotation("coder-x", AnnotationDecision.INCLUDE)],
    )

    with pytest.raises(ValidationError, match="unknown operators"):
        DefectCorpus(
            corpus_id="invalid",
            protocol_version="1.0",
            operator_catalog=["evaluator_flip"],
            annotator_ids=["coder-x"],
            records=[record],
        )

    data = _verified("annotation-operator").model_dump()
    data["annotations"] = [
        _annotation(
            "coder-x",
            AnnotationDecision.INCLUDE,
            operator="missing",
        ).model_dump()
    ]
    record = DefectRecord.model_validate(data)
    with pytest.raises(ValidationError, match="annotations reference unknown"):
        DefectCorpus(
            corpus_id="invalid",
            protocol_version="1.0",
            operator_catalog=["evaluator_flip"],
            annotator_ids=["coder-x"],
            records=[record],
        )


def test_corpus_rejects_unknown_duplicate_target_and_naive_freeze_time():
    duplicate = DefectRecord(
        defect_id="duplicate",
        title="Duplicate",
        source_repository="owner/runtime",
        summary="Duplicates another report.",
        status=DefectStatus.EXCLUDED,
        exclusion_reason="duplicate",
        duplicate_of="missing",
    )

    with pytest.raises(ValidationError, match="unknown duplicate target"):
        DefectCorpus(
            corpus_id="invalid-duplicate",
            protocol_version="1.0",
            records=[duplicate],
        )
    with pytest.raises(ValidationError, match="timezone offset"):
        DefectCorpus(
            corpus_id="naive-time",
            protocol_version="1.0",
            frozen_at=datetime.now(),
        )


def test_corpus_rejects_unknown_annotator():
    record = _verified(
        "unknown-annotator",
        annotations=[
            _annotation("missing-coder", AnnotationDecision.INCLUDE)
        ],
    )
    with pytest.raises(ValidationError, match="unknown annotators"):
        DefectCorpus(
            corpus_id="invalid",
            protocol_version="1.0",
            operator_catalog=["evaluator_flip"],
            annotator_ids=["coder-x"],
            records=[record],
        )


def test_frozen_corpus_rejects_unresolved_candidates():
    candidate = DefectRecord(
        defect_id="candidate",
        title="Candidate",
        source_repository="owner/runtime",
        summary="Needs adjudication.",
    )

    with pytest.raises(ValidationError, match="unresolved defects"):
        DefectCorpus(
            corpus_id="frozen",
            protocol_version="1.0",
            records=[candidate],
            frozen_at=datetime.now(timezone.utc),
        )


def test_summary_reports_partitions_mappings_and_two_coder_agreement():
    records = [
        _verified(
            "agree-include",
            annotations=[
                _annotation(
                    "a",
                    AnnotationDecision.INCLUDE,
                    boundary=DefectBoundary.EVALUATOR,
                    operator="evaluator_flip",
                ),
                _annotation(
                    "b",
                    AnnotationDecision.INCLUDE,
                    boundary=DefectBoundary.EVALUATOR,
                    operator="evaluator_flip",
                ),
            ],
        ),
        _verified(
            "agree-exclude",
            partition=CorpusPartition.VALIDATION,
            operator=None,
            annotations=[
                _annotation("a", AnnotationDecision.EXCLUDE),
                _annotation("b", AnnotationDecision.EXCLUDE),
            ],
        ),
        _verified(
            "disagree-left",
            annotations=[
                _annotation("a", AnnotationDecision.INCLUDE),
                _annotation("b", AnnotationDecision.EXCLUDE),
            ],
        ),
        _verified(
            "disagree-right",
            annotations=[
                _annotation("a", AnnotationDecision.EXCLUDE),
                _annotation("b", AnnotationDecision.INCLUDE),
            ],
        ),
    ]
    corpus = DefectCorpus(
        corpus_id="agreement",
        protocol_version="1.0",
        operator_catalog=["evaluator_flip"],
        annotator_ids=["a", "b"],
        records=records,
    )

    summary = DefectCorpusAnalyzer.summarize(
        corpus,
        annotator_pair=("a", "b"),
    )

    assert summary.total_records == 4
    assert summary.verified_by_partition == {"derivation": 3, "validation": 1}
    assert summary.verified_operator_counts == {"evaluator_flip": 3}
    assert summary.unmapped_verified_ids == ["agree-exclude"]
    assert summary.unmapped_validation_ids == ["agree-exclude"]
    assert summary.validation_mapping_rate == 0.0
    assert summary.agreement.sample_size == 4
    assert summary.agreement.observed_decision_agreement == 0.5
    assert summary.agreement.expected_decision_agreement == 0.5
    assert summary.agreement.cohens_kappa == 0.0
    assert summary.agreement.mean_boundary_jaccard == 1.0
    assert summary.agreement.mean_operator_jaccard == 1.0


def test_readiness_exposes_unresolved_data_and_unsupported_operators():
    verified = _verified(
        "verified",
        annotations=[
            _annotation("a", AnnotationDecision.INCLUDE),
            _annotation("b", AnnotationDecision.INCLUDE),
        ],
    )
    candidate = DefectRecord(
        defect_id="candidate",
        title="Candidate",
        source_repository="owner/runtime",
        summary="Still unresolved.",
    )
    corpus = DefectCorpus(
        corpus_id="not-ready",
        protocol_version="1.0",
        operator_catalog=["evaluator_flip", "tool_result_stale"],
        annotator_ids=["a", "b"],
        records=[verified, candidate],
    )

    report = DefectCorpusAnalyzer.assess_readiness(
        corpus,
        minimum_verified=2,
        minimum_validation=1,
        minimum_double_coded=2,
        annotator_pair=("a", "b"),
    )

    assert report.ready is False
    assert report.unsupported_operator_ids == ["tool_result_stale"]
    assert len(report.blockers) == 5


def test_readiness_passes_only_when_all_preregistered_gates_hold():
    annotations = [
        _annotation("a", AnnotationDecision.INCLUDE),
        _annotation("b", AnnotationDecision.INCLUDE),
    ]
    corpus = DefectCorpus(
        corpus_id="ready",
        protocol_version="1.0",
        operator_catalog=["evaluator_flip", "tool_result_stale"],
        annotator_ids=["a", "b"],
        records=[
            _verified("derive", annotations=annotations),
            _verified(
                "derive-tool",
                operator="tool_result_stale",
                annotations=annotations,
            ),
            _verified(
                "validate",
                partition=CorpusPartition.VALIDATION,
                operator="tool_result_stale",
                annotations=annotations,
            ),
        ],
    )

    report = DefectCorpusAnalyzer.assess_readiness(
        corpus,
        minimum_verified=3,
        minimum_validation=1,
        minimum_double_coded=2,
        annotator_pair=("a", "b"),
    )

    assert report.ready is True
    assert report.blockers == []


def test_defect_cli_emits_machine_readable_summary(capsys):
    path = Path(__file__).parents[1] / "research" / "defects" / "corpus.json"

    exit_code = defect_cli_main([str(path)])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["summary"]["status_counts"] == {"candidate": 1}


def test_defect_cli_fails_readiness_gate_for_draft_seed(capsys):
    path = Path(__file__).parents[1] / "research" / "defects" / "corpus.json"

    exit_code = defect_cli_main([
        str(path),
        "--check-readiness",
        "--minimum-verified",
        "1",
        "--minimum-validation",
        "1",
        "--minimum-double-coded",
        "1",
    ])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert output["readiness"]["ready"] is False
