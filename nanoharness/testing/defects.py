"""Versioned real-defect corpus models and agreement/readiness analysis."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, Field, model_validator


DEFECT_CORPUS_SCHEMA_VERSION = 1


class DefectStatus(str, Enum):
    """Adjudicated lifecycle state of a collected defect candidate."""

    CANDIDATE = "candidate"
    VERIFIED = "verified"
    EXCLUDED = "excluded"
    DISPUTED = "disputed"


class CorpusPartition(str, Enum):
    """Separation used to avoid deriving and validating on the same defects."""

    DERIVATION = "derivation"
    VALIDATION = "validation"


class DefectBoundary(str, Enum):
    """Agent component boundaries used for stratified analysis."""

    MODEL = "model"
    TOOL = "tool"
    CONTEXT = "context"
    STATE = "state"
    HOOK = "hook"
    EVALUATOR = "evaluator"
    PERMISSION = "permission"
    CONTROL_FLOW = "control_flow"
    REPLAY = "replay"
    OTHER = "other"


class EvidenceKind(str, Enum):
    """Independent roles that evidence can play in defect verification."""

    ISSUE_REPORT = "issue_report"
    FIXING_CHANGE = "fixing_change"
    REGRESSION_TEST = "regression_test"
    REPRODUCTION = "reproduction"
    RELEASE_NOTE = "release_note"
    DISCUSSION = "discussion"
    ARCHIVED_ARTIFACT = "archived_artifact"


class AnnotationDecision(str, Enum):
    """Independent coder decision before adjudication."""

    INCLUDE = "include"
    EXCLUDE = "exclude"
    UNCERTAIN = "uncertain"


class CodingPass(str, Enum):
    """Blind human-coding phase."""

    PASS_A = "pass_a"
    PASS_B = "pass_b"


class CoderCompletionDeclaration(BaseModel):
    """A human coder's declaration that a submission is complete and blind."""

    completed_at: datetime
    independent: bool
    packet_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_timezone_and_independence(self):
        if self.completed_at.utcoffset() is None:
            raise ValueError("completed_at must include a timezone offset")
        if not self.independent:
            raise ValueError("completed submission must confirm independence")
        return self


class DefectCodingEntry(BaseModel):
    """One independent human judgment in a blind coding submission."""

    defect_id: str = Field(min_length=1)
    decision: Optional[AnnotationDecision] = None
    exclusion_reason: str = ""
    boundaries: List[DefectBoundary] = Field(default_factory=list)
    operator_ids: List[str] = Field(default_factory=list)
    trigger: str = ""
    symptom: str = ""
    root_cause: str = ""
    impact: str = ""
    evidence_ids: List[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)


class DefectCodingSubmission(BaseModel):
    """Digest-bound Pass A or Pass B judgments from one human coder."""

    schema_version: int = Field(default=1, ge=1)
    corpus_id: str = Field(min_length=1)
    pass_id: CodingPass
    coder_id: str = Field(min_length=1)
    manual_version: str = Field(min_length=1)
    packet_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    entries: List[DefectCodingEntry]
    completion: Optional[CoderCompletionDeclaration] = None

    @model_validator(mode="after")
    def validate_pass(self):
        entry_ids = [entry.defect_id for entry in self.entries]
        if len(entry_ids) != len(set(entry_ids)):
            raise ValueError("coding entry IDs must be unique")
        if self.pass_id is CodingPass.PASS_A:
            if any(entry.operator_ids for entry in self.entries):
                raise ValueError("Pass A cannot contain operator labels")
            if any(entry.decision is None for entry in self.entries):
                raise ValueError("Pass A requires a decision for every entry")
        if self.pass_id is CodingPass.PASS_B:
            if any(entry.decision is not None for entry in self.entries):
                raise ValueError("Pass B cannot revise Pass A decisions")
        if (
            self.completion is not None
            and self.completion.packet_sha256 != self.packet_sha256
        ):
            raise ValueError("completion packet digest must match submission")
        return self


class DefectEvidence(BaseModel):
    """Stable locator for one source used to audit a defect claim."""

    evidence_id: str = Field(min_length=1)
    kind: EvidenceKind
    locator: str = Field(min_length=1)
    repository: Optional[str] = None
    revision: Optional[str] = None
    path: Optional[str] = None
    test_id: Optional[str] = None
    accessed_on: Optional[date] = None
    archived_sha256: Optional[str] = Field(
        default=None,
        pattern=r"^[0-9a-fA-F]{64}$",
    )
    notes: str = ""


class DefectAnnotation(BaseModel):
    """One coder's independent inclusion and taxonomy judgment."""

    annotator_id: str = Field(min_length=1)
    decision: AnnotationDecision
    boundaries: List[DefectBoundary] = Field(default_factory=list)
    operator_ids: List[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
    annotated_on: Optional[date] = None

    @model_validator(mode="after")
    def validate_labels(self):
        if len(set(self.boundaries)) != len(self.boundaries):
            raise ValueError("Annotation boundaries must be unique")
        if len(set(self.operator_ids)) != len(self.operator_ids):
            raise ValueError("Annotation operator IDs must be unique")
        return self


class DefectRecord(BaseModel):
    """Auditable defect candidate, evidence, coding, and final adjudication."""

    defect_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source_repository: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    status: DefectStatus = DefectStatus.CANDIDATE
    partition: Optional[CorpusPartition] = None
    boundaries: List[DefectBoundary] = Field(default_factory=list)
    symptoms: List[str] = Field(default_factory=list)
    trigger: str = ""
    root_cause: str = ""
    impact: str = ""
    evidence: List[DefectEvidence] = Field(default_factory=list)
    operator_ids: List[str] = Field(default_factory=list)
    annotations: List[DefectAnnotation] = Field(default_factory=list)
    decision_rationale: str = ""
    exclusion_reason: str = ""
    duplicate_of: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_record(self):
        if len(set(self.boundaries)) != len(self.boundaries):
            raise ValueError("Defect boundaries must be unique")
        if len(set(self.operator_ids)) != len(self.operator_ids):
            raise ValueError("Defect operator IDs must be unique")
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("Defect evidence IDs must be unique")
        annotator_ids = [item.annotator_id for item in self.annotations]
        if len(set(annotator_ids)) != len(annotator_ids):
            raise ValueError("A defect may have only one annotation per annotator")

        if self.status is DefectStatus.VERIFIED:
            self._validate_verified()
        if self.status is DefectStatus.EXCLUDED and not self.exclusion_reason:
            raise ValueError("Excluded defects require exclusion_reason")
        if self.duplicate_of == self.defect_id:
            raise ValueError("A defect cannot be a duplicate of itself")
        return self

    def _validate_verified(self) -> None:
        if self.partition is None:
            raise ValueError("Verified defects require a corpus partition")
        if not self.boundaries:
            raise ValueError("Verified defects require at least one boundary")
        if not self.trigger:
            raise ValueError("Verified defects require a trigger description")
        if not self.root_cause:
            raise ValueError("Verified defects require a root-cause description")
        if not self.decision_rationale:
            raise ValueError("Verified defects require a decision rationale")
        evidence_kinds = {item.kind for item in self.evidence}
        if len(evidence_kinds) < 2:
            raise ValueError("Verified defects require at least two evidence kinds")
        confirming = {
            EvidenceKind.FIXING_CHANGE,
            EvidenceKind.REGRESSION_TEST,
            EvidenceKind.REPRODUCTION,
        }
        if not evidence_kinds.intersection(confirming):
            raise ValueError("Verified defects require confirmatory evidence")


class DefectCorpus(BaseModel):
    """Versioned corpus with a frozen operator catalog and coder identities."""

    schema_version: int = Field(default=DEFECT_CORPUS_SCHEMA_VERSION, ge=1)
    corpus_id: str = Field(min_length=1)
    protocol_version: str = Field(min_length=1)
    operator_catalog: List[str] = Field(default_factory=list)
    annotator_ids: List[str] = Field(default_factory=list)
    records: List[DefectRecord] = Field(default_factory=list)
    frozen_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_corpus(self):
        if len(set(self.operator_catalog)) != len(self.operator_catalog):
            raise ValueError("Operator catalog IDs must be unique")
        if len(set(self.annotator_ids)) != len(self.annotator_ids):
            raise ValueError("Corpus annotator IDs must be unique")
        record_ids = [record.defect_id for record in self.records]
        if len(set(record_ids)) != len(record_ids):
            raise ValueError("Defect IDs must be unique within a corpus")
        known_records = set(record_ids)
        known_operators = set(self.operator_catalog)
        known_annotators = set(self.annotator_ids)
        for record in self.records:
            unknown_operators = set(record.operator_ids) - known_operators
            if unknown_operators:
                raise ValueError(
                    f"Defect {record.defect_id!r} references unknown operators: "
                    f"{sorted(unknown_operators)}"
                )
            unknown_annotators = {
                annotation.annotator_id for annotation in record.annotations
            } - known_annotators
            if unknown_annotators:
                raise ValueError(
                    f"Defect {record.defect_id!r} references unknown annotators: "
                    f"{sorted(unknown_annotators)}"
                )
            unknown_annotation_operators = {
                operator_id
                for annotation in record.annotations
                for operator_id in annotation.operator_ids
            } - known_operators
            if unknown_annotation_operators:
                raise ValueError(
                    f"Defect {record.defect_id!r} annotations reference unknown "
                    f"operators: {sorted(unknown_annotation_operators)}"
                )
            if record.duplicate_of and record.duplicate_of not in known_records:
                raise ValueError(
                    f"Defect {record.defect_id!r} has unknown duplicate target "
                    f"{record.duplicate_of!r}"
                )
        if self.frozen_at is not None:
            if self.frozen_at.utcoffset() is None:
                raise ValueError("frozen_at must include a timezone offset")
            unresolved = [
                record.defect_id
                for record in self.records
                if record.status in {DefectStatus.CANDIDATE, DefectStatus.DISPUTED}
            ]
            if unresolved:
                raise ValueError(
                    f"Frozen corpora cannot contain unresolved defects: {unresolved}"
                )
        return self


class AgreementSummary(BaseModel):
    """Two-coder agreement for decisions and multi-label assignments."""

    annotator_a: str
    annotator_b: str
    sample_size: int = Field(ge=0)
    observed_decision_agreement: Optional[float] = None
    expected_decision_agreement: Optional[float] = None
    cohens_kappa: Optional[float] = None
    mean_boundary_jaccard: Optional[float] = None
    mean_operator_jaccard: Optional[float] = None


class DefectCorpusSummary(BaseModel):
    """Descriptive corpus counts kept separate from inferential analysis."""

    corpus_id: str
    total_records: int = Field(ge=0)
    status_counts: Dict[str, int] = Field(default_factory=dict)
    verified_by_partition: Dict[str, int] = Field(default_factory=dict)
    verified_boundary_counts: Dict[str, int] = Field(default_factory=dict)
    verified_operator_counts: Dict[str, int] = Field(default_factory=dict)
    verified_evidence_counts: Dict[str, int] = Field(default_factory=dict)
    unmapped_verified_ids: List[str] = Field(default_factory=list)
    unmapped_validation_ids: List[str] = Field(default_factory=list)
    validation_mapping_rate: Optional[float] = None
    agreement: Optional[AgreementSummary] = None


class CorpusReadinessReport(BaseModel):
    """Explicit pre-freeze gates for a planned empirical study."""

    ready: bool
    blockers: List[str] = Field(default_factory=list)
    verified_count: int = Field(ge=0)
    validation_count: int = Field(ge=0)
    double_coded_count: int = Field(ge=0)
    unsupported_operator_ids: List[str] = Field(default_factory=list)


class DefectCorpusAnalyzer:
    """Deterministic summaries, coder agreement, and freeze-readiness checks."""

    @classmethod
    def summarize(
        cls,
        corpus: DefectCorpus,
        *,
        annotator_pair: Optional[Sequence[str]] = None,
    ) -> DefectCorpusSummary:
        verified = [
            record
            for record in corpus.records
            if record.status is DefectStatus.VERIFIED
        ]
        status_counts = Counter(record.status.value for record in corpus.records)
        partitions = Counter(
            record.partition.value
            for record in verified
            if record.partition is not None
        )
        boundaries = Counter(
            boundary.value for record in verified for boundary in record.boundaries
        )
        operators = Counter(
            operator_id for record in verified for operator_id in record.operator_ids
        )
        evidence = Counter(
            item.kind.value for record in verified for item in record.evidence
        )
        validation = [
            record
            for record in verified
            if record.partition is CorpusPartition.VALIDATION
        ]
        unmapped_validation = [
            record.defect_id for record in validation if not record.operator_ids
        ]
        agreement = None
        if annotator_pair is not None:
            if len(annotator_pair) != 2:
                raise ValueError("annotator_pair must contain exactly two IDs")
            agreement = cls.agreement(corpus, annotator_pair[0], annotator_pair[1])
        return DefectCorpusSummary(
            corpus_id=corpus.corpus_id,
            total_records=len(corpus.records),
            status_counts=dict(status_counts),
            verified_by_partition=dict(partitions),
            verified_boundary_counts=dict(boundaries),
            verified_operator_counts=dict(operators),
            verified_evidence_counts=dict(evidence),
            unmapped_verified_ids=[
                record.defect_id for record in verified if not record.operator_ids
            ],
            unmapped_validation_ids=unmapped_validation,
            validation_mapping_rate=(
                (len(validation) - len(unmapped_validation)) / len(validation)
                if validation
                else None
            ),
            agreement=agreement,
        )

    @staticmethod
    def agreement(
        corpus: DefectCorpus,
        annotator_a: str,
        annotator_b: str,
    ) -> AgreementSummary:
        if annotator_a == annotator_b:
            raise ValueError("Agreement requires two distinct annotators")
        unknown = {annotator_a, annotator_b} - set(corpus.annotator_ids)
        if unknown:
            raise ValueError(f"Unknown annotators: {sorted(unknown)}")
        pairs = []
        for record in corpus.records:
            by_annotator = {
                annotation.annotator_id: annotation
                for annotation in record.annotations
            }
            if annotator_a in by_annotator and annotator_b in by_annotator:
                pairs.append((by_annotator[annotator_a], by_annotator[annotator_b]))
        if not pairs:
            return AgreementSummary(
                annotator_a=annotator_a,
                annotator_b=annotator_b,
                sample_size=0,
            )

        observed = sum(left.decision is right.decision for left, right in pairs) / len(
            pairs
        )
        categories = list(AnnotationDecision)
        left_counts = Counter(left.decision for left, _ in pairs)
        right_counts = Counter(right.decision for _, right in pairs)
        expected = sum(
            (left_counts[category] / len(pairs))
            * (right_counts[category] / len(pairs))
            for category in categories
        )
        kappa = None if expected == 1.0 else (observed - expected) / (1 - expected)
        return AgreementSummary(
            annotator_a=annotator_a,
            annotator_b=annotator_b,
            sample_size=len(pairs),
            observed_decision_agreement=observed,
            expected_decision_agreement=expected,
            cohens_kappa=kappa,
            mean_boundary_jaccard=sum(
                _jaccard(left.boundaries, right.boundaries) for left, right in pairs
            )
            / len(pairs),
            mean_operator_jaccard=sum(
                _jaccard(left.operator_ids, right.operator_ids)
                for left, right in pairs
            )
            / len(pairs),
        )

    @staticmethod
    def assess_readiness(
        corpus: DefectCorpus,
        *,
        minimum_verified: int,
        minimum_validation: int,
        minimum_double_coded: int,
        annotator_pair: Optional[Sequence[str]] = None,
    ) -> CorpusReadinessReport:
        if min(minimum_verified, minimum_validation, minimum_double_coded) < 0:
            raise ValueError("Readiness thresholds must be non-negative")
        verified = [
            record
            for record in corpus.records
            if record.status is DefectStatus.VERIFIED
        ]
        validation_count = sum(
            record.partition is CorpusPartition.VALIDATION for record in verified
        )
        agreement = (
            DefectCorpusAnalyzer.summarize(
                corpus,
                annotator_pair=annotator_pair,
            ).agreement
            if annotator_pair is not None
            else None
        )
        double_coded = agreement.sample_size if agreement is not None else 0
        supported = {
            operator_id
            for record in verified
            if record.partition is CorpusPartition.DERIVATION
            for operator_id in record.operator_ids
        }
        unsupported = sorted(set(corpus.operator_catalog) - supported)
        unresolved = [
            record.defect_id
            for record in corpus.records
            if record.status in {DefectStatus.CANDIDATE, DefectStatus.DISPUTED}
        ]
        blockers = []
        if len(verified) < minimum_verified:
            blockers.append(
                f"verified defects {len(verified)} below minimum {minimum_verified}"
            )
        if validation_count < minimum_validation:
            blockers.append(
                f"validation defects {validation_count} below minimum "
                f"{minimum_validation}"
            )
        if double_coded < minimum_double_coded:
            blockers.append(
                f"double-coded defects {double_coded} below minimum "
                f"{minimum_double_coded}"
            )
        if unsupported:
            blockers.append(f"operators without verified defects: {unsupported}")
        if unresolved:
            blockers.append(f"unresolved defect candidates: {unresolved}")
        return CorpusReadinessReport(
            ready=not blockers,
            blockers=blockers,
            verified_count=len(verified),
            validation_count=validation_count,
            double_coded_count=double_coded,
            unsupported_operator_ids=unsupported,
        )


def _jaccard(left: Sequence[Any], right: Sequence[Any]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set and not right_set:
        return 1.0
    return len(left_set.intersection(right_set)) / len(left_set.union(right_set))
