"""Validate the byte-level inputs and outputs of formal agent dispatch."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Mapping, Optional

from nanoharness.testing.defects import DefectCodingSubmission


FROZEN_MODEL_ID = "gpt-5.6-sol"
FROZEN_MODEL_CONFIGURATION = {"reasoning_effort": "high"}
FROZEN_FORK_TURNS = "none"
AGENT_IDS = ("A1", "A2", "A3")
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


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _load_object(path: Path, label: str) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _git_bytes(repo: Path, revision: str, path: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(repo), "show", f"{revision}:{path}"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode:
        raise ValueError(f"missing revision-bound input: {revision}:{path}")
    return completed.stdout


def _repo_root(path: Path) -> Path:
    completed = subprocess.run(
        ["git", "-C", str(path.parent), "rev-parse", "--show-toplevel"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode:
        raise ValueError("binding must be inside a Git worktree")
    return Path(completed.stdout.strip()).resolve()


def _resolve_repo_path(repo: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError("bound path must be a non-empty string")
    resolved = (repo / relative).resolve()
    try:
        resolved.relative_to(repo)
    except ValueError as error:
        raise ValueError(f"bound path escapes repository: {relative}") from error
    return resolved


def _manifest_entries(path: Path) -> dict[str, str]:
    entries = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, relative = line.split("  ", 1)
        if relative in entries:
            raise ValueError(f"duplicate checksum entry: {relative}")
        entries[relative] = digest
    return entries


def _verify_file_record(
    record: Mapping[str, Any],
    repo: Path,
) -> Path:
    relative = record.get("path")
    expected = record.get("sha256")
    path = _resolve_repo_path(repo, relative)
    if _sha256_file(path) != expected:
        raise ValueError(f"bound input digest mismatch: {relative}")
    return path


def _verify_revision_records(
    records: list[Mapping[str, Any]], repo: Path, freeze_revision: str
) -> None:
    by_path = {record["path"]: record["sha256"] for record in records}
    tree = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-r", "--full-tree", freeze_revision],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if tree.returncode:
        raise ValueError("freeze payload revision does not contain all bound inputs")
    object_by_path = {}
    for line in tree.stdout.splitlines():
        metadata, relative = line.split("\t", 1)
        object_by_path[relative] = metadata.split()[2]
    if not set(by_path) <= set(object_by_path):
        raise ValueError("freeze payload revision input set mismatch")
    object_ids = [object_by_path[relative] for relative in by_path]
    batch = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "--batch"],
        input="".join(f"{object_id}\n" for object_id in object_ids).encode(),
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    position = 0
    for relative, expected in by_path.items():
        header_end = batch.index(b"\n", position)
        header = batch[position:header_end].decode().split()
        size = int(header[2])
        start = header_end + 1
        payload = batch[start : start + size]
        position = start + size + 1
        if _sha256_bytes(payload) != expected:
            raise ValueError(f"freeze payload revision mismatch: {relative}")


def _find_paper_repo(repo: Path) -> Path:
    for parent in (repo, *repo.parents):
        candidate = parent / "AgentMutationTestingPaper"
        if candidate.is_dir():
            return candidate
    raise ValueError("AgentMutationTestingPaper repository not found")


def _validate_binding_payload(
    binding_path: Path,
    *,
    paper_repo: Optional[Path],
    require_clean: bool,
) -> tuple[dict[str, Any], Path, str]:
    binding_path = binding_path.resolve()
    repo = _repo_root(binding_path)
    corpus = binding_path.parents[2]
    try:
        binding_relative = binding_path.relative_to(corpus).as_posix()
    except ValueError as error:
        raise ValueError("binding must be inside real_corpus_v1") from error
    top_manifest = corpus / "SHA256SUMS"
    top_entries = _manifest_entries(top_manifest)
    binding_sha256 = _sha256_file(binding_path)
    if top_entries.get(binding_relative) != binding_sha256:
        raise ValueError("binding SHA-256 is not bound by top-level manifest")

    binding = _load_object(binding_path, "dispatch binding")
    if binding.get("protocol_id") != "agent-review-v1":
        raise ValueError("unexpected frozen protocol")
    if binding.get("model_id") != FROZEN_MODEL_ID:
        raise ValueError("unexpected frozen model")
    if binding.get("model_configuration") != FROZEN_MODEL_CONFIGURATION:
        raise ValueError("unexpected frozen model configuration")
    if binding.get("fork_turns") != FROZEN_FORK_TURNS:
        raise ValueError("unexpected frozen fork_turns policy")
    freeze_revision = binding.get("freeze_payload_revision")
    if not isinstance(freeze_revision, str) or len(freeze_revision) != 40:
        raise ValueError("invalid freeze payload revision")

    inputs = binding.get("inputs")
    if not isinstance(inputs, dict):
        raise ValueError("binding inputs must be an object")
    records = [
        inputs.get("prompt"),
        inputs.get("protocol"),
        inputs.get("formal_inventory"),
        inputs.get("packet"),
    ]
    if not all(isinstance(record, dict) for record in records):
        raise ValueError("binding is missing a required NanoHarness input")
    formal_inventory_relative = Path(inputs["formal_inventory"]["path"]).relative_to(
        corpus.relative_to(repo)
    ).as_posix()
    if (
        top_entries.get(formal_inventory_relative)
        != inputs["formal_inventory"]["sha256"]
    ):
        raise ValueError("nested formal inventory is not bound by top-level manifest")
    nano_records = [inputs[name] for name in (
        "prompt", "protocol", "formal_inventory", "packet"
    )]
    resolved = {
        name: _verify_file_record(inputs[name], repo)
        for name in ("prompt", "protocol", "formal_inventory", "packet")
    }
    templates = inputs.get("templates")
    if not isinstance(templates, dict) or tuple(sorted(templates)) != AGENT_IDS:
        raise ValueError("binding must contain exactly A1, A2, A3 templates")
    template_paths = {
        agent_id: _verify_file_record(templates[agent_id], repo)
        for agent_id in AGENT_IDS
    }
    nano_records.extend(templates.values())
    tooling = inputs.get("tooling")
    expected_tooling_paths = {
        ".gitattributes",
        "research/defects/real_corpus_v1/agent_dispatch.py",
        "research/defects/real_corpus_v1/agent_dispatch_cli.py",
    }
    if (
        not isinstance(tooling, list)
        or {record.get("path") for record in tooling} != expected_tooling_paths
    ):
        raise ValueError("binding must contain exact dispatch tooling")
    for record in tooling:
        _verify_file_record(record, repo)
    nano_records.extend(tooling)
    patches = inputs.get("patches")
    if not isinstance(patches, list) or len(patches) != 77:
        raise ValueError("binding must contain exactly 77 patches")
    patch_ids = []
    for record in patches:
        if not isinstance(record, dict) or not isinstance(
            record.get("defect_id"), str
        ):
            raise ValueError("invalid patch binding")
        patch_ids.append(record["defect_id"])
        _verify_file_record(record, repo)
        nano_records.append(record)
    if len(set(patch_ids)) != 77:
        raise ValueError("patch bindings must have unique defect IDs")
    _verify_revision_records(nano_records, repo, freeze_revision)

    packet = _load_object(resolved["packet"], "evidence packet")
    candidates = packet.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("evidence packet candidates must be a list")
    packet_ids = [candidate.get("defect_id") for candidate in candidates]
    if packet_ids != patch_ids:
        raise ValueError("patch bindings must match packet defect order")
    for agent_id, path in template_paths.items():
        template = _load_object(path, f"{agent_id} template")
        template_ids = [
            entry.get("defect_id") for entry in template.get("entries", [])
        ]
        if template_ids != packet_ids:
            raise ValueError(f"{agent_id} template IDs do not match packet order")

    formal_inventory = _manifest_entries(resolved["formal_inventory"])
    formal_root = resolved["formal_inventory"].parent
    expected_formal = {
        Path(inputs["prompt"]["path"]).relative_to(
            Path(inputs["formal_inventory"]["path"]).parent
        ).as_posix(): inputs["prompt"]["sha256"],
        Path(inputs["protocol"]["path"]).relative_to(
            Path(inputs["formal_inventory"]["path"]).parent
        ).as_posix(): inputs["protocol"]["sha256"],
    }
    expected_formal.update(
        {
            path.relative_to(formal_root).as_posix(): templates[agent_id]["sha256"]
            for agent_id, path in template_paths.items()
        }
    )
    if formal_inventory != expected_formal:
        raise ValueError("nested formal inventory does not match bound inputs")

    protocol = _load_object(resolved["protocol"], "protocol")
    provenance = protocol.get("dispatch_time_provenance", {})
    if provenance.get("model_id") != FROZEN_MODEL_ID:
        raise ValueError("protocol frozen model mismatch")
    if provenance.get("model_configuration") != FROZEN_MODEL_CONFIGURATION:
        raise ValueError("protocol frozen model configuration mismatch")
    if provenance.get("prompt_sha256") != inputs["prompt"]["sha256"]:
        raise ValueError("protocol prompt digest mismatch")
    if protocol.get("packet_sha256") != inputs["packet"]["sha256"]:
        raise ValueError("protocol packet digest mismatch")

    paper = inputs.get("paper")
    if not isinstance(paper, dict):
        raise ValueError("binding is missing Paper inputs")
    revision = paper.get("revision")
    paper_repo = paper_repo.resolve() if paper_repo else _find_paper_repo(repo)
    for label in ("manual", "instructions"):
        record = paper.get(label)
        if not isinstance(record, dict):
            raise ValueError(f"binding is missing Paper {label}")
        actual = _sha256_bytes(_git_bytes(paper_repo, revision, record.get("path")))
        if actual != record.get("sha256"):
            raise ValueError(f"Paper {label} revision-bound digest mismatch")

    if require_clean:
        status = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout
        if status:
            raise ValueError("tracked files are not clean")
    return binding, repo, binding_sha256


def preflight_dispatch(
    *,
    binding_path: Path,
    task_name: str,
    expected_annotator: str,
    expected_template: str,
    expected_output: str,
    phase: str,
    expected_binding_sha256: Optional[str] = None,
    expected_freeze_payload_revision: Optional[str] = None,
    paper_repo: Optional[Path] = None,
) -> dict[str, Any]:
    """Validate frozen dispatch bytes and resolve one canonical assignment."""

    binding_path = Path(binding_path)
    binding, _, binding_sha256 = _validate_binding_payload(
        binding_path,
        paper_repo=Path(paper_repo) if paper_repo else None,
        require_clean=True,
    )
    if phase not in {"start", "end"}:
        raise ValueError("phase must be start or end")
    if phase == "end":
        if binding_sha256 != expected_binding_sha256:
            raise ValueError("binding changed since start")
        if binding["freeze_payload_revision"] != expected_freeze_payload_revision:
            raise ValueError("freeze payload revision changed since start")
    suffix = task_name.rsplit("/", 1)[-1]
    mapping = binding.get("canonical_task_mapping", {})
    assignment = mapping.get(suffix)
    if not isinstance(assignment, dict):
        raise ValueError("unknown canonical task name")
    expected = {
        "annotator_id": expected_annotator,
        "template": expected_template,
        "output": expected_output,
    }
    if assignment != expected:
        raise ValueError("expected assignment does not match canonical task mapping")
    return {
        "valid": True,
        "phase": phase,
        "binding_sha256": binding_sha256,
        "freeze_payload_revision": binding["freeze_payload_revision"],
        "model_id": binding["model_id"],
        "model_configuration": binding["model_configuration"],
        "fork_turns": binding["fork_turns"],
        "assignment": assignment,
    }


def _reject_unexpected_fields(payload: Mapping[str, Any]) -> None:
    extras = set(payload) - SUBMISSION_FIELDS
    if extras:
        raise ValueError(f"unexpected field: {sorted(extras)[0]}")
    for index, entry in enumerate(payload.get("entries", [])):
        extras = set(entry) - ENTRY_FIELDS
        if extras:
            raise ValueError(f"entry {index} unexpected field: {sorted(extras)[0]}")
    for field, allowed in (
        ("agent_provenance", PROVENANCE_FIELDS),
        ("completion", COMPLETION_FIELDS),
    ):
        value = payload.get(field)
        if isinstance(value, dict):
            extras = set(value) - allowed
            if extras:
                raise ValueError(f"{field} unexpected field: {sorted(extras)[0]}")


def validate_frozen_submission(
    *,
    binding_path: Path,
    protocol_path: Path,
    expected_annotator: str,
    template_path: Path,
    submission_path: Path,
) -> dict[str, Any]:
    """Validate one raw Pass A submission against external frozen values."""

    binding, repo, _ = _validate_binding_payload(
        Path(binding_path), paper_repo=None, require_clean=False
    )
    inputs = binding["inputs"]
    if expected_annotator not in AGENT_IDS:
        raise ValueError("invalid expected annotator")
    if Path(protocol_path).resolve() != _resolve_repo_path(
        repo, inputs["protocol"]["path"]
    ):
        raise ValueError("protocol path is not the bound protocol")
    if Path(template_path).resolve() != _resolve_repo_path(
        repo, inputs["templates"][expected_annotator]["path"]
    ):
        raise ValueError("template path is not bound to expected annotator")

    protocol = _load_object(Path(protocol_path), "protocol")
    template = _load_object(Path(template_path), "template")
    raw = _load_object(Path(submission_path), "submission")
    _reject_unexpected_fields(raw)
    if raw.get("coder_id") != expected_annotator:
        raise ValueError("coder identity does not match expected annotator")
    if set(raw) != set(template):
        raise ValueError("submission shape does not match template")
    template_entries = template.get("entries", [])
    raw_entries = raw.get("entries", [])
    if [entry.get("defect_id") for entry in raw_entries] != [
        entry.get("defect_id") for entry in template_entries
    ]:
        raise ValueError("template entry order mismatch")
    if any(set(entry) != set(template_entry) for entry, template_entry in zip(
        raw_entries, template_entries
    )):
        raise ValueError("submission entry shape does not match template")
    for field in (
        "schema_version",
        "corpus_id",
        "pass_id",
        "manual_version",
        "packet_sha256",
    ):
        if raw.get(field) != template.get(field):
            raise ValueError(f"frozen {field.replace('_', ' ')} mismatch")
    if raw.get("manual_version") != protocol.get("manual_version"):
        raise ValueError("frozen manual version mismatch")
    if any(entry.get("operator_ids") != [] for entry in raw_entries):
        raise ValueError("Pass A operator_ids must remain empty")

    provenance = raw.get("agent_provenance")
    if not isinstance(provenance, dict):
        raise ValueError("agent provenance is required")
    if provenance.get("annotator_id") != expected_annotator:
        raise ValueError("provenance coder identity mismatch")
    if provenance.get("protocol_id") != binding["protocol_id"]:
        raise ValueError("frozen protocol mismatch")
    if provenance.get("model_id") != binding["model_id"]:
        raise ValueError("frozen model mismatch")
    if provenance.get("prompt_sha256") != inputs["prompt"]["sha256"]:
        raise ValueError("frozen prompt digest mismatch")
    if provenance.get("input_sha256") != inputs["packet"]["sha256"]:
        raise ValueError("frozen packet digest mismatch")
    if provenance.get("artifact_revision") != binding["freeze_payload_revision"]:
        raise ValueError("frozen artifact revision mismatch")
    if raw.get("completion") is None:
        raise ValueError("completion is required")

    packet = _load_object(
        _resolve_repo_path(repo, inputs["packet"]["path"]), "evidence packet"
    )
    evidence_by_defect = {
        candidate["defect_id"]: {
            evidence["evidence_id"] for evidence in candidate.get("evidence", [])
        }
        for candidate in packet["candidates"]
    }
    for entry in raw_entries:
        evidence_ids = entry.get("evidence_ids")
        if (
            not isinstance(evidence_ids, list)
            or not evidence_ids
            or not set(evidence_ids) <= evidence_by_defect[entry["defect_id"]]
        ):
            raise ValueError(f"invalid evidence IDs for {entry['defect_id']}")
    DefectCodingSubmission.model_validate(raw)
    return {
        "valid": True,
        "annotator_id": expected_annotator,
        "entry_count": len(raw_entries),
    }
