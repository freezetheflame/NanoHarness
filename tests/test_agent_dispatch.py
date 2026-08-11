import hashlib
import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", repo, *args],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


@pytest.fixture(scope="module")
def frozen_dispatch(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("frozen-dispatch")
    nano = tmp_path / "NanoHarness"
    paper = tmp_path / "AgentMutationTestingPaper"
    for repo in (nano, paper):
        repo.mkdir()
        _git(repo, "init")
        _git(repo, "config", "user.email", "test@example.test")
        _git(repo, "config", "user.name", "Test")
    attributes = nano / ".gitattributes"
    attributes.write_text(
        "research/defects/real_corpus_v1/agent_dispatch*.py text eol=lf\n"
        "research/defects/real_corpus_v1/formal/agent-review-v1/"
        "AGENT_PROMPT.md text eol=lf\n",
        encoding="utf-8",
        newline="\n",
    )

    manual_path = "experiments/design/DEFECT_CODING_MANUAL.md"
    instructions_path = (
        "experiments/defects/real-corpus-v1/AGENT_ANNOTATOR_INSTRUCTIONS.md"
    )
    (paper / manual_path).parent.mkdir(parents=True)
    (paper / manual_path).write_text(
        "manual 2.0\n", encoding="utf-8", newline="\n"
    )
    (paper / instructions_path).parent.mkdir(parents=True)
    (paper / instructions_path).write_text(
        "instructions\n", encoding="utf-8", newline="\n"
    )
    _git(paper, "add", ".")
    _git(paper, "commit", "-m", "paper inputs")
    paper_revision = _git(paper, "rev-parse", "HEAD")

    corpus = nano / "research/defects/real_corpus_v1"
    formal = corpus / "formal/agent-review-v1"
    tooling_paths = [
        attributes,
        corpus / "agent_dispatch.py",
        corpus / "agent_dispatch_cli.py",
        corpus / "agent_review.py",
        corpus / "agent_review_cli.py",
    ]
    for path in tooling_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {path.name}\n", encoding="utf-8", newline="\n")
    prompt = formal / "AGENT_PROMPT.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("identical prompt\n", encoding="utf-8", newline="\n")
    packet = corpus / "evidence_packet.json"
    defect_ids = [f"D{index:03d}" for index in range(1, 78)]
    patch_paths = {}
    for defect_id in defect_ids:
        path = corpus / f"patches/{defect_id}.patch"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"patch {defect_id}\n", encoding="utf-8", newline="\n"
        )
        patch_paths[defect_id] = path
    candidates = [
        {
            "defect_id": defect_id,
            "evidence": [{"evidence_id": f"{defect_id}-fix"}],
            "patch_path": f"patches/{defect_id}.patch",
            "patch_sha256": _sha(patch_paths[defect_id]),
        }
        for defect_id in defect_ids
    ]
    _write_json(packet, {"candidates": candidates})

    template_paths = {}
    for annotator_id in ("A1", "A2", "A3"):
        path = formal / f"templates/{annotator_id}/pass_a.json"
        template_paths[annotator_id] = path
        _write_json(
            path,
            {
                "schema_version": 1,
                "corpus_id": "agent-real-defects-v1",
                "pass_id": "pass_a",
                "coder_id": annotator_id,
                "manual_version": "2.0",
                "packet_sha256": _sha(packet),
                "entries": [
                    {
                        "defect_id": defect_id,
                        "decision": None,
                        "exclusion_reason": "",
                        "boundaries": [],
                        "operator_ids": [],
                        "trigger": "",
                        "symptom": "",
                        "root_cause": "",
                        "impact": "",
                        "evidence_ids": [],
                        "rationale": "",
                    }
                    for defect_id in defect_ids
                ],
                "completion": None,
                "agent_provenance": {
                    "protocol_id": "agent-review-v1",
                    "annotator_id": annotator_id,
                    "model_id": None,
                    "prompt_sha256": None,
                    "input_sha256": _sha(packet),
                    "artifact_revision": None,
                    "started_at": None,
                },
            },
        )

    protocol = formal / "protocol.json"
    protocol_payload = {
        "protocol_id": "agent-review-v1",
        "manual_version": "2.0",
        "packet_sha256": _sha(packet),
        "dispatch_time_provenance": {
            "model_id": "gpt-5.6-sol",
            "model_configuration": {"reasoning_effort": "high"},
            "prompt_sha256": _sha(prompt),
        },
    }
    _write_json(protocol, protocol_payload)
    nested = formal / "SHA256SUMS"
    nested.write_text(
        "".join(
            f"{_sha(path)}  {path.relative_to(formal).as_posix()}\n"
            for path in sorted(
                [prompt, protocol, *template_paths.values()],
                key=lambda item: item.relative_to(formal).as_posix(),
            )
        ),
        encoding="utf-8",
        newline="\n",
    )
    _git(nano, "add", ".")
    _git(nano, "commit", "-m", "freeze payload")
    freeze_revision = _git(nano, "rev-parse", "HEAD")

    rel = lambda path: path.relative_to(nano).as_posix()
    mapping = {
        f"formal_a{index}": {
            "annotator_id": annotator_id,
            "template": rel(template_paths[annotator_id]),
            "output": rel(formal / f"pass_a/{annotator_id}.json"),
        }
        for index, annotator_id in enumerate(("A1", "A2", "A3"), start=1)
    }
    binding = formal / "DISPATCH_BINDING.json"
    binding_payload = {
        "schema_version": 1,
        "protocol_id": "agent-review-v1",
        "freeze_payload_revision": freeze_revision,
        "model_id": "gpt-5.6-sol",
        "model_configuration": {"reasoning_effort": "high"},
        "fork_turns": "none",
        "canonical_task_mapping": mapping,
        "inputs": {
            "prompt": {"path": rel(prompt), "sha256": _sha(prompt)},
            "protocol": {"path": rel(protocol), "sha256": _sha(protocol)},
            "formal_inventory": {"path": rel(nested), "sha256": _sha(nested)},
            "packet": {"path": rel(packet), "sha256": _sha(packet)},
            "tooling": [
                {"path": rel(path), "sha256": _sha(path)} for path in tooling_paths
            ],
            "templates": {
                key: {"path": rel(path), "sha256": _sha(path)}
                for key, path in template_paths.items()
            },
            "patches": [
                {
                    "defect_id": defect_id,
                    "path": rel(path),
                    "sha256": _sha(path),
                }
                for defect_id, path in sorted(patch_paths.items())
            ],
            "paper": {
                "revision": paper_revision,
                "manual": {"path": manual_path, "sha256": _sha(paper / manual_path)},
                "instructions": {
                    "path": instructions_path,
                    "sha256": _sha(paper / instructions_path),
                },
            },
        },
    }
    _write_json(binding, binding_payload)
    top_manifest = corpus / "SHA256SUMS"
    top_manifest.write_text(
        f"{_sha(binding)}  formal/agent-review-v1/DISPATCH_BINDING.json\n"
        f"{_sha(nested)}  formal/agent-review-v1/SHA256SUMS\n",
        encoding="utf-8",
        newline="\n",
    )
    _git(nano, "add", ".")
    _git(nano, "commit", "-m", "bind dispatch")

    submission = formal / "pass_a/A1.json"
    submission_payload = deepcopy(json.loads(template_paths["A1"].read_text()))
    for entry in submission_payload["entries"]:
        entry.update(
            {
                "decision": "include",
                "boundaries": ["tool"],
                "trigger": "trigger",
                "symptom": "symptom",
                "root_cause": "root cause",
                "impact": "impact",
                "evidence_ids": [f"{entry['defect_id']}-fix"],
                "rationale": "reason",
            }
        )
    submission_payload["completion"] = {
        "completed_at": "2026-08-10T10:00:00+08:00",
        "independent": True,
        "packet_sha256": _sha(packet),
    }
    submission_payload["agent_provenance"].update(
        {
            "model_id": "gpt-5.6-sol",
            "prompt_sha256": _sha(prompt),
            "artifact_revision": freeze_revision,
            "started_at": "2026-08-10T09:01:00+08:00",
        }
    )
    return {
        "nano": nano,
        "paper": paper,
        "binding": binding,
        "binding_payload": binding_payload,
        "protocol": protocol,
        "template": template_paths["A1"],
        "submission": submission,
        "submission_payload": submission_payload,
        "freeze_revision": freeze_revision,
        "formal": formal,
        "packet": packet,
        "prompt": prompt,
        "nested": nested,
        "patch": patch_paths[defect_ids[0]],
        "review_code": corpus / "agent_review.py",
        "review_cli": corpus / "agent_review_cli.py",
        "top_manifest": top_manifest,
    }


def _validation_identity(frozen_dispatch, dispatch_record=None):
    from research.defects.real_corpus_v1.agent_dispatch import (
        workspace_snapshot_sha256,
    )

    if dispatch_record is not None:
        payload = json.loads(dispatch_record.read_text(encoding="utf-8"))
        return {
            "expected_binding_sha256": payload["binding_sha256"],
            "expected_freeze_payload_revision": payload[
                "freeze_payload_revision"
            ],
            "expected_start_workspace_sha256": payload[
                "workspace_snapshot_sha256"
            ],
        }
    output = frozen_dispatch["binding_payload"]["canonical_task_mapping"][
        "formal_a1"
    ]["output"]
    return {
        "expected_binding_sha256": _sha(frozen_dispatch["binding"]),
        "expected_freeze_payload_revision": frozen_dispatch["freeze_revision"],
        "expected_start_workspace_sha256": workspace_snapshot_sha256(
            frozen_dispatch["nano"], exclude_untracked=output
        ),
    }


def _create_test_dispatch_record(frozen_dispatch):
    from research.defects.real_corpus_v1.agent_dispatch import create_dispatch_record

    record = frozen_dispatch["formal"] / "DISPATCH_RECORD.json"
    batch_preflight, assignments = _dispatch_receipts(frozen_dispatch)
    create_dispatch_record(
        binding_path=frozen_dispatch["binding"],
        record_path=record,
        actual_payload_path=frozen_dispatch["prompt"],
        batch_preflight=batch_preflight,
        assignments=assignments,
        paper_repo=frozen_dispatch["paper"],
    )
    return record


def _dispatch_assignments():
    return [
        {
            "annotator_id": annotator_id,
            "canonical_task_name": f"formal_a{index}",
            "returned_task_id": f"task-{index}",
            "started_at": f"2026-08-10T09:0{index}:00+08:00",
        }
        for index, annotator_id in enumerate(("A1", "A2", "A3"), start=1)
    ]


def _dispatch_receipts(frozen_dispatch):
    from research.defects.real_corpus_v1.agent_dispatch import (
        preflight_batch,
        preflight_dispatch,
    )

    batch = preflight_batch(
        binding_path=frozen_dispatch["binding"],
        checked_at="2026-08-10T09:00:00+08:00",
        paper_repo=frozen_dispatch["paper"],
    )
    assignments = _dispatch_assignments()
    mapping = frozen_dispatch["binding_payload"]["canonical_task_mapping"]
    for index, assignment in enumerate(assignments, start=1):
        mapped = mapping[assignment["canonical_task_name"]]
        start = preflight_dispatch(
            binding_path=frozen_dispatch["binding"],
            task_name=assignment["canonical_task_name"],
            expected_annotator=assignment["annotator_id"],
            expected_template=mapped["template"],
            expected_output=mapped["output"],
            phase="start",
            checked_at=f"2026-08-10T09:1{index}:00+08:00",
            paper_repo=frozen_dispatch["paper"],
        )
        assignment["start_preflight"] = {
            "checked_at": start["checked_at"],
            "workspace_snapshot_sha256": start["workspace_snapshot_sha256"],
        }
    return batch["batch_preflight"], assignments


def test_create_and_validate_dispatch_record(frozen_dispatch):
    from research.defects.real_corpus_v1.agent_dispatch import (
        create_dispatch_record,
        validate_dispatch_record,
    )

    record = frozen_dispatch["formal"] / "DISPATCH_RECORD.json"
    try:
        batch_preflight, assignments = _dispatch_receipts(frozen_dispatch)
        created = create_dispatch_record(
            binding_path=frozen_dispatch["binding"],
            record_path=record,
            actual_payload_path=frozen_dispatch["prompt"],
            batch_preflight=batch_preflight,
            assignments=assignments,
            paper_repo=frozen_dispatch["paper"],
        )
        payload = json.loads(record.read_text(encoding="utf-8"))
        assert created == payload
        assert payload["schema_version"] == 1
        assert payload["protocol_id"] == "agent-review-v1"
        assert payload["binding_sha256"] == _sha(frozen_dispatch["binding"])
        assert payload["freeze_payload_revision"] == frozen_dispatch["freeze_revision"]
        assert payload["prompt_sha256"] == _sha(frozen_dispatch["prompt"])
        assert payload["actual_payload_sha256"] == _sha(frozen_dispatch["prompt"])
        assert payload["batch_preflight"] == batch_preflight
        assert len(payload["workspace_snapshot_sha256"]) == 64
        assert validate_dispatch_record(
            binding_path=frozen_dispatch["binding"],
            record_path=record,
            paper_repo=frozen_dispatch["paper"],
        ) == {
            "valid": True,
            "binding_sha256": payload["binding_sha256"],
            "freeze_payload_revision": payload["freeze_payload_revision"],
            "workspace_snapshot_sha256": payload["workspace_snapshot_sha256"],
            "batch_preflight": payload["batch_preflight"],
            "assignments": payload["assignments"],
        }
    finally:
        if record.exists():
            record.unlink()


def test_create_dispatch_record_rejects_non_prompt_payload(frozen_dispatch):
    from research.defects.real_corpus_v1.agent_dispatch import create_dispatch_record

    record = frozen_dispatch["formal"] / "DISPATCH_RECORD.json"
    wrong_payload = frozen_dispatch["formal"] / "wrong-payload.md"
    wrong_payload.write_text("different bytes\n", encoding="utf-8", newline="\n")
    try:
        batch_preflight, assignments = _dispatch_receipts(frozen_dispatch)
        with pytest.raises(ValueError, match="actual payload must equal prompt bytes"):
            create_dispatch_record(
                binding_path=frozen_dispatch["binding"],
                record_path=record,
                actual_payload_path=wrong_payload,
                batch_preflight=batch_preflight,
                assignments=assignments,
                paper_repo=frozen_dispatch["paper"],
            )
    finally:
        wrong_payload.unlink()
        if record.exists():
            record.unlink()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda item: item["assignments"][0].update(
                canonical_task_name="formal_a2"
            ),
            "canonical task mapping",
        ),
        (
            lambda item: item["assignments"][1].update(returned_task_id="task-1"),
            "returned task IDs must be unique",
        ),
        (lambda item: item["assignments"].pop(), "exactly A1, A2, A3"),
        (lambda item: item.update(model_id="wrong"), "frozen model"),
        (
            lambda item: item.update(model_configuration={"reasoning_effort": "low"}),
            "model configuration",
        ),
        (lambda item: item.update(fork_turns="all"), "fork_turns"),
        (lambda item: item.update(prompt_sha256="0" * 64), "prompt digest"),
        (lambda item: item.update(binding_sha256="0" * 64), "binding digest"),
        (
            lambda item: item.update(freeze_payload_revision="a" * 40),
            "freeze payload revision",
        ),
        (
            lambda item: item.update(actual_payload_sha256="0" * 64),
            "actual payload digest",
        ),
        (
            lambda item: item["assignments"][0].update(
                started_at="2026-08-10T09:01:00"
            ),
            "timezone-aware",
        ),
        (
            lambda item: item["batch_preflight"].update(receipt_sha256="0" * 64),
            "batch preflight receipt digest",
        ),
        (
            lambda item: item["assignments"][0]["start_preflight"].update(
                workspace_snapshot_sha256="0" * 64
            ),
            "start preflight workspace snapshot",
        ),
        (
            lambda item: item["assignments"][0].update(
                started_at="2026-08-10T08:59:59+08:00"
            ),
            "dispatch timestamp order",
        ),
        (
            lambda item: item["assignments"][0]["start_preflight"].update(
                checked_at="2026-08-10T09:00:30+08:00"
            ),
            "dispatch timestamp order",
        ),
    ],
)
def test_validate_dispatch_record_rejects_tampering(
    frozen_dispatch, mutation, message
):
    from research.defects.real_corpus_v1.agent_dispatch import (
        create_dispatch_record,
        validate_dispatch_record,
    )

    record = frozen_dispatch["formal"] / "DISPATCH_RECORD.json"
    try:
        batch_preflight, assignments = _dispatch_receipts(frozen_dispatch)
        payload = create_dispatch_record(
            binding_path=frozen_dispatch["binding"],
            record_path=record,
            actual_payload_path=frozen_dispatch["prompt"],
            batch_preflight=batch_preflight,
            assignments=assignments,
            paper_repo=frozen_dispatch["paper"],
        )
        mutation(payload)
        _write_json(record, payload)
        with pytest.raises(ValueError, match=message):
            validate_dispatch_record(
                binding_path=frozen_dispatch["binding"],
                record_path=record,
                paper_repo=frozen_dispatch["paper"],
            )
    finally:
        if record.exists():
            record.unlink()


@pytest.mark.parametrize(
    ("task_started_at", "start_checked_at", "valid"),
    [
        ("2026-08-10T00:59:59Z", "2026-08-10T09:11:00+08:00", False),
        ("2026-08-10T03:12:00Z", "2026-08-10T09:11:00+08:00", False),
        ("2026-08-10T02:00:00Z", "2026-08-10T11:00:00+08:00", True),
        ("2026-08-10T09:00:00+08:00", "2026-08-10T09:00:00+08:00", True),
    ],
)
def test_create_dispatch_record_enforces_absolute_timestamp_order(
    frozen_dispatch, task_started_at, start_checked_at, valid
):
    from research.defects.real_corpus_v1.agent_dispatch import create_dispatch_record

    batch, assignments = _dispatch_receipts(frozen_dispatch)
    assignments[0]["started_at"] = task_started_at
    assignments[0]["start_preflight"]["checked_at"] = start_checked_at
    record = frozen_dispatch["formal"] / "DISPATCH_RECORD.json"
    try:
        if valid:
            assert create_dispatch_record(
                binding_path=frozen_dispatch["binding"],
                record_path=record,
                actual_payload_path=frozen_dispatch["prompt"],
                batch_preflight=batch,
                assignments=assignments,
                paper_repo=frozen_dispatch["paper"],
            )["assignments"][0]["started_at"] == task_started_at
        else:
            with pytest.raises(ValueError, match="dispatch timestamp order"):
                create_dispatch_record(
                    binding_path=frozen_dispatch["binding"],
                    record_path=record,
                    actual_payload_path=frozen_dispatch["prompt"],
                    batch_preflight=batch,
                    assignments=assignments,
                    paper_repo=frozen_dispatch["paper"],
                )
    finally:
        if record.exists():
            record.unlink()


def test_preflight_returns_machine_readable_frozen_assignment(frozen_dispatch):
    from research.defects.real_corpus_v1.agent_dispatch import (
        create_dispatch_record,
        preflight_dispatch,
    )

    record = frozen_dispatch["formal"] / "DISPATCH_RECORD.json"
    try:
        batch_preflight, assignments = _dispatch_receipts(frozen_dispatch)
        dispatch = create_dispatch_record(
            binding_path=frozen_dispatch["binding"],
            record_path=record,
            actual_payload_path=frozen_dispatch["prompt"],
            batch_preflight=batch_preflight,
            assignments=assignments,
            paper_repo=frozen_dispatch["paper"],
        )
        result = preflight_dispatch(
            binding_path=frozen_dispatch["binding"],
            dispatch_record_path=record,
            task_name="/root/formal_a1",
            expected_annotator="A1",
            expected_template=frozen_dispatch["binding_payload"][
                "canonical_task_mapping"
            ]["formal_a1"]["template"],
            expected_output=frozen_dispatch["binding_payload"][
                "canonical_task_mapping"
            ]["formal_a1"]["output"],
            phase="start",
            paper_repo=frozen_dispatch["paper"],
        )
    finally:
        if record.exists():
            record.unlink()

    assert result["valid"] is True
    assert result["assignment"]["annotator_id"] == "A1"
    assert result["binding_sha256"] == _sha(frozen_dispatch["binding"])
    assert result["freeze_payload_revision"] == frozen_dispatch["freeze_revision"]
    assert result["workspace_snapshot_sha256"] == dispatch[
        "workspace_snapshot_sha256"
    ]


def test_end_preflight_rejects_replaced_binding_identity(frozen_dispatch):
    from research.defects.real_corpus_v1.agent_dispatch import preflight_dispatch

    with pytest.raises(ValueError, match="binding changed since start"):
        preflight_dispatch(
            binding_path=frozen_dispatch["binding"],
            task_name="formal_a1",
            expected_annotator="A1",
            expected_template=frozen_dispatch["binding_payload"][
                "canonical_task_mapping"
            ]["formal_a1"]["template"],
            expected_output=frozen_dispatch["binding_payload"][
                "canonical_task_mapping"
            ]["formal_a1"]["output"],
            phase="end",
            expected_binding_sha256="0" * 64,
            expected_freeze_payload_revision=frozen_dispatch["freeze_revision"],
            expected_start_workspace_sha256="0" * 64,
            paper_repo=frozen_dispatch["paper"],
        )


def test_preflight_start_rejects_existing_agent_output(frozen_dispatch):
    from research.defects.real_corpus_v1.agent_dispatch import preflight_dispatch

    output = frozen_dispatch["submission"]
    _write_json(output, frozen_dispatch["submission_payload"])
    assignment = frozen_dispatch["binding_payload"]["canonical_task_mapping"][
        "formal_a1"
    ]
    try:
        with pytest.raises(ValueError, match="output already exists"):
            preflight_dispatch(
                binding_path=frozen_dispatch["binding"],
                task_name="formal_a1",
                expected_annotator="A1",
                expected_template=assignment["template"],
                expected_output=assignment["output"],
                phase="start",
                paper_repo=frozen_dispatch["paper"],
            )
    finally:
        output.unlink()


def test_end_preflight_requires_only_mapped_output_change(frozen_dispatch):
    from research.defects.real_corpus_v1.agent_dispatch import preflight_dispatch

    assignment = frozen_dispatch["binding_payload"]["canonical_task_mapping"][
        "formal_a1"
    ]
    start = preflight_dispatch(
        binding_path=frozen_dispatch["binding"],
        task_name="formal_a1",
        expected_annotator="A1",
        expected_template=assignment["template"],
        expected_output=assignment["output"],
        phase="start",
        paper_repo=frozen_dispatch["paper"],
    )
    _write_json(frozen_dispatch["submission"], frozen_dispatch["submission_payload"])
    try:
        end = preflight_dispatch(
            binding_path=frozen_dispatch["binding"],
            task_name="formal_a1",
            expected_annotator="A1",
            expected_template=assignment["template"],
            expected_output=assignment["output"],
            phase="end",
            expected_binding_sha256=start["binding_sha256"],
            expected_freeze_payload_revision=start["freeze_payload_revision"],
            expected_start_workspace_sha256=start["workspace_snapshot_sha256"],
            paper_repo=frozen_dispatch["paper"],
        )
        assert end["workspace_snapshot_sha256"] == start["workspace_snapshot_sha256"]
    finally:
        frozen_dispatch["submission"].unlink()


def test_end_preflight_rejects_missing_output_or_extra_untracked(frozen_dispatch):
    from research.defects.real_corpus_v1.agent_dispatch import preflight_dispatch

    assignment = frozen_dispatch["binding_payload"]["canonical_task_mapping"][
        "formal_a1"
    ]
    start = preflight_dispatch(
        binding_path=frozen_dispatch["binding"],
        task_name="formal_a1",
        expected_annotator="A1",
        expected_template=assignment["template"],
        expected_output=assignment["output"],
        phase="start",
        paper_repo=frozen_dispatch["paper"],
    )
    end_args = {
        "binding_path": frozen_dispatch["binding"],
        "task_name": "formal_a1",
        "expected_annotator": "A1",
        "expected_template": assignment["template"],
        "expected_output": assignment["output"],
        "phase": "end",
        "expected_binding_sha256": start["binding_sha256"],
        "expected_freeze_payload_revision": start["freeze_payload_revision"],
        "expected_start_workspace_sha256": start["workspace_snapshot_sha256"],
        "paper_repo": frozen_dispatch["paper"],
    }
    with pytest.raises(ValueError, match="mapped output is missing"):
        preflight_dispatch(**end_args)
    _write_json(frozen_dispatch["submission"], frozen_dispatch["submission_payload"])
    extra = frozen_dispatch["formal"] / "unexpected.tmp"
    extra.write_text("extra", encoding="utf-8")
    try:
        with pytest.raises(ValueError, match="workspace changed since start"):
            preflight_dispatch(**end_args)
    finally:
        frozen_dispatch["submission"].unlink()
        extra.unlink()


def test_concurrent_outputs_are_allowlisted_after_batch_gate(frozen_dispatch):
    from research.defects.real_corpus_v1.agent_dispatch import (
        create_dispatch_record,
        preflight_batch,
        preflight_dispatch,
    )

    mapping = frozen_dispatch["binding_payload"]["canonical_task_mapping"]
    batch = preflight_batch(
        binding_path=frozen_dispatch["binding"],
        checked_at="2026-08-10T09:00:00+08:00",
        paper_repo=frozen_dispatch["paper"],
    )["batch_preflight"]
    starts = {}
    outputs = []
    for index, (task_name, mapped) in enumerate(mapping.items(), start=1):
        start = preflight_dispatch(
            binding_path=frozen_dispatch["binding"],
            task_name=task_name,
            expected_annotator=mapped["annotator_id"],
            expected_template=mapped["template"],
            expected_output=mapped["output"],
            phase="start",
            checked_at=f"2026-08-10T09:1{index}:00+08:00",
            paper_repo=frozen_dispatch["paper"],
        )
        starts[task_name] = start
        output = frozen_dispatch["nano"] / mapped["output"]
        _write_json(output, {"annotator": mapped["annotator_id"]})
        outputs.append(output)
    assignments = _dispatch_assignments()
    for assignment in assignments:
        start = starts[assignment["canonical_task_name"]]
        assignment["start_preflight"] = {
            "checked_at": start["checked_at"],
            "workspace_snapshot_sha256": start["workspace_snapshot_sha256"],
        }
    record = frozen_dispatch["formal"] / "DISPATCH_RECORD.json"
    try:
        create_dispatch_record(
            binding_path=frozen_dispatch["binding"],
            record_path=record,
            actual_payload_path=frozen_dispatch["prompt"],
            batch_preflight=batch,
            assignments=assignments,
            paper_repo=frozen_dispatch["paper"],
        )
        for task_name, mapped in mapping.items():
            start = starts[task_name]
            result = preflight_dispatch(
                binding_path=frozen_dispatch["binding"],
                dispatch_record_path=record,
                task_name=task_name,
                expected_annotator=mapped["annotator_id"],
                expected_template=mapped["template"],
                expected_output=mapped["output"],
                phase="end",
                expected_binding_sha256=start["binding_sha256"],
                expected_freeze_payload_revision=start["freeze_payload_revision"],
                expected_start_workspace_sha256=start[
                    "workspace_snapshot_sha256"
                ],
                paper_repo=frozen_dispatch["paper"],
            )
            assert result["valid"] is True
    finally:
        for output in outputs:
            if output.exists():
                output.unlink()
        if record.exists():
            record.unlink()


@pytest.mark.parametrize("change", ["modify", "delete"])
def test_workspace_snapshot_rejects_preexisting_untracked_metadata_change(
    frozen_dispatch, change
):
    from research.defects.real_corpus_v1.agent_dispatch import preflight_dispatch

    existing = frozen_dispatch["formal"] / "preexisting.tmp"
    existing.write_text("before", encoding="utf-8")
    mapped = frozen_dispatch["binding_payload"]["canonical_task_mapping"][
        "formal_a1"
    ]
    output = frozen_dispatch["nano"] / mapped["output"]
    try:
        start = preflight_dispatch(
            binding_path=frozen_dispatch["binding"],
            task_name="formal_a1",
            expected_annotator="A1",
            expected_template=mapped["template"],
            expected_output=mapped["output"],
            phase="start",
            checked_at="2026-08-10T09:11:00+08:00",
            paper_repo=frozen_dispatch["paper"],
        )
        _write_json(output, {"annotator": "A1"})
        if change == "modify":
            existing.write_text("after-and-larger", encoding="utf-8")
        else:
            existing.unlink()
        with pytest.raises(ValueError, match="workspace changed since start"):
            preflight_dispatch(
                binding_path=frozen_dispatch["binding"],
                task_name="formal_a1",
                expected_annotator="A1",
                expected_template=mapped["template"],
                expected_output=mapped["output"],
                phase="end",
                expected_binding_sha256=start["binding_sha256"],
                expected_freeze_payload_revision=start[
                    "freeze_payload_revision"
                ],
                expected_start_workspace_sha256=start[
                    "workspace_snapshot_sha256"
                ],
                paper_repo=frozen_dispatch["paper"],
            )
    finally:
        if output.exists():
            output.unlink()
        if existing.exists():
            existing.unlink()


def test_preflight_rejects_unknown_task_and_tampered_binding(frozen_dispatch):
    from research.defects.real_corpus_v1.agent_dispatch import preflight_dispatch

    with pytest.raises(ValueError, match="unknown canonical task name"):
        preflight_dispatch(
            binding_path=frozen_dispatch["binding"],
            task_name="formal_ax",
            expected_annotator="A1",
            expected_template="x",
            expected_output="y",
            phase="start",
            paper_repo=frozen_dispatch["paper"],
        )
    payload = frozen_dispatch["binding_payload"]
    payload["model_id"] = "wrong"
    _write_json(frozen_dispatch["binding"], payload)
    try:
        with pytest.raises(ValueError, match="not bound by top-level manifest"):
            preflight_dispatch(
                binding_path=frozen_dispatch["binding"],
                task_name="formal_a1",
                expected_annotator="A1",
                expected_template=payload["canonical_task_mapping"]["formal_a1"][
                    "template"
                ],
                expected_output=payload["canonical_task_mapping"]["formal_a1"][
                    "output"
                ],
                phase="start",
                paper_repo=frozen_dispatch["paper"],
            )
    finally:
        payload["model_id"] = "gpt-5.6-sol"
        _write_json(frozen_dispatch["binding"], payload)


@pytest.mark.parametrize(
    "path_key",
    [
        "prompt",
        "protocol",
        "nested",
        "template",
        "packet",
        "patch",
        "review_code",
        "review_cli",
    ],
)
def test_preflight_rejects_tampered_nanoharness_input(
    frozen_dispatch, path_key
):
    from research.defects.real_corpus_v1.agent_dispatch import preflight_dispatch

    path = frozen_dispatch[path_key]
    original = path.read_bytes()
    path.write_bytes(original + b"tamper")
    assignment = frozen_dispatch["binding_payload"]["canonical_task_mapping"][
        "formal_a1"
    ]
    try:
        with pytest.raises(ValueError, match="digest mismatch"):
            preflight_dispatch(
                binding_path=frozen_dispatch["binding"],
                task_name="formal_a1",
                expected_annotator="A1",
                expected_template=assignment["template"],
                expected_output=assignment["output"],
                phase="start",
                paper_repo=frozen_dispatch["paper"],
            )
    finally:
        path.write_bytes(original)


@pytest.mark.parametrize("paper_key", ["manual", "instructions"])
def test_preflight_rejects_tampered_paper_binding(frozen_dispatch, paper_key):
    from research.defects.real_corpus_v1.agent_dispatch import preflight_dispatch

    original_binding = frozen_dispatch["binding"].read_bytes()
    original_manifest = frozen_dispatch["top_manifest"].read_bytes()
    payload = deepcopy(frozen_dispatch["binding_payload"])
    payload["inputs"]["paper"][paper_key]["sha256"] = "0" * 64
    _write_json(frozen_dispatch["binding"], payload)
    frozen_dispatch["top_manifest"].write_text(
        f"{_sha(frozen_dispatch['binding'])}  "
        "formal/agent-review-v1/DISPATCH_BINDING.json\n"
        f"{_sha(frozen_dispatch['nested'])}  "
        "formal/agent-review-v1/SHA256SUMS\n",
        encoding="utf-8",
        newline="\n",
    )
    assignment = payload["canonical_task_mapping"]["formal_a1"]
    try:
        with pytest.raises(ValueError, match=f"Paper {paper_key}.*digest mismatch"):
            preflight_dispatch(
                binding_path=frozen_dispatch["binding"],
                task_name="formal_a1",
                expected_annotator="A1",
                expected_template=assignment["template"],
                expected_output=assignment["output"],
                phase="start",
                paper_repo=frozen_dispatch["paper"],
            )
    finally:
        frozen_dispatch["binding"].write_bytes(original_binding)
        frozen_dispatch["top_manifest"].write_bytes(original_manifest)


def test_preflight_rejects_dirty_tracked_files(frozen_dispatch):
    from research.defects.real_corpus_v1.agent_dispatch import preflight_dispatch

    manifest = frozen_dispatch["top_manifest"]
    original = manifest.read_bytes()
    manifest.write_bytes(original + ("0" * 64 + "  unrelated.txt\n").encode())
    assignment = frozen_dispatch["binding_payload"]["canonical_task_mapping"][
        "formal_a1"
    ]
    try:
        with pytest.raises(ValueError, match="tracked files are not clean"):
            preflight_dispatch(
                binding_path=frozen_dispatch["binding"],
                task_name="formal_a1",
                expected_annotator="A1",
                expected_template=assignment["template"],
                expected_output=assignment["output"],
                phase="start",
                paper_repo=frozen_dispatch["paper"],
            )
    finally:
        manifest.write_bytes(original)


def test_retained_dispatch_binding_rejects_restart_after_raw_freeze():
    from research.defects.real_corpus_v1.agent_dispatch import preflight_dispatch

    repo = Path(__file__).parents[1]
    formal = repo / "research/defects/real_corpus_v1/formal/agent-review-v1"
    binding = formal / "DISPATCH_BINDING.json"
    payload = json.loads(binding.read_text(encoding="utf-8"))
    for suffix, assignment in payload["canonical_task_mapping"].items():
        with pytest.raises(ValueError, match="own mapped output already exists"):
            preflight_dispatch(
                binding_path=binding,
                task_name=f"/root/{suffix}",
                expected_annotator=assignment["annotator_id"],
                expected_template=assignment["template"],
                expected_output=assignment["output"],
                phase="start",
            )


def test_frozen_submission_validation_accepts_exact_bound_submission(
    frozen_dispatch,
):
    from research.defects.real_corpus_v1.agent_dispatch import (
        validate_frozen_submission,
    )

    record = _create_test_dispatch_record(frozen_dispatch)
    _write_json(
        frozen_dispatch["submission"], frozen_dispatch["submission_payload"]
    )
    try:
        result = validate_frozen_submission(
            binding_path=frozen_dispatch["binding"],
            dispatch_record_path=record,
            protocol_path=frozen_dispatch["protocol"],
            expected_annotator="A1",
            template_path=frozen_dispatch["template"],
            submission_path=frozen_dispatch["submission"],
            **_validation_identity(frozen_dispatch, record),
        )
    finally:
        frozen_dispatch["submission"].unlink()
        record.unlink()

    assert result == {"annotator_id": "A1", "entry_count": 77, "valid": True}


def test_frozen_submission_validation_rejects_saved_identity_swap(
    frozen_dispatch,
):
    from research.defects.real_corpus_v1.agent_dispatch import (
        validate_frozen_submission,
    )

    record = _create_test_dispatch_record(frozen_dispatch)
    _write_json(
        frozen_dispatch["submission"], frozen_dispatch["submission_payload"]
    )
    identity = _validation_identity(frozen_dispatch, record)
    identity["expected_binding_sha256"] = "0" * 64
    try:
        with pytest.raises(ValueError, match="saved binding identity"):
            validate_frozen_submission(
                binding_path=frozen_dispatch["binding"],
                dispatch_record_path=record,
                protocol_path=frozen_dispatch["protocol"],
                expected_annotator="A1",
                template_path=frozen_dispatch["template"],
                submission_path=frozen_dispatch["submission"],
                **identity,
            )
    finally:
        frozen_dispatch["submission"].unlink()
        record.unlink()


def test_dispatch_cli_runs_preflight_and_submission_validation(
    frozen_dispatch,
    capsys,
):
    from research.defects.real_corpus_v1.agent_dispatch_cli import main

    assignment = frozen_dispatch["binding_payload"]["canonical_task_mapping"][
        "formal_a1"
    ]
    record = frozen_dispatch["formal"] / "DISPATCH_RECORD.json"
    batch_preflight, dispatch_assignments = _dispatch_receipts(frozen_dispatch)
    create_args = [
        "create-dispatch-record",
        "--binding",
        str(frozen_dispatch["binding"]),
        "--record",
        str(record),
        "--actual-payload",
        str(frozen_dispatch["prompt"]),
        "--batch-preflight-json",
        json.dumps(batch_preflight),
    ]
    for item in dispatch_assignments:
        create_args.extend(
            [
                "--assignment",
                item["annotator_id"],
                item["canonical_task_name"],
                item["returned_task_id"],
                item["started_at"],
                item["start_preflight"]["checked_at"],
                item["start_preflight"]["workspace_snapshot_sha256"],
            ]
        )
    create_args.extend(["--paper-repo", str(frozen_dispatch["paper"])])
    assert main(create_args) == 0
    dispatch = json.loads(capsys.readouterr().out)

    exit_code = main(
        [
            "preflight",
            "--binding",
            str(frozen_dispatch["binding"]),
            "--dispatch-record",
            str(record),
            "--task-name",
            "formal_a1",
            "--expected-annotator",
            "A1",
            "--expected-template",
            assignment["template"],
            "--expected-output",
            assignment["output"],
            "--phase",
            "start",
            "--paper-repo",
            str(frozen_dispatch["paper"]),
        ]
    )
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["binding_sha256"] == _sha(
        frozen_dispatch["binding"]
    )

    _write_json(
        frozen_dispatch["submission"], frozen_dispatch["submission_payload"]
    )
    exit_code = main(
        [
            "validate-submission",
            "--binding",
            str(frozen_dispatch["binding"]),
            "--dispatch-record",
            str(record),
            "--protocol",
            str(frozen_dispatch["protocol"]),
            "--expected-annotator",
            "A1",
            "--template",
            str(frozen_dispatch["template"]),
            "--submission",
            str(frozen_dispatch["submission"]),
            "--expected-binding-sha256",
            dispatch["binding_sha256"],
            "--expected-freeze-payload-revision",
            dispatch["freeze_payload_revision"],
            "--expected-start-workspace-sha256",
            dispatch["workspace_snapshot_sha256"],
        ]
    )
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True
    frozen_dispatch["submission"].unlink()
    record.unlink()


def _write_complete_review_outputs(frozen_dispatch):
    record = _create_test_dispatch_record(frozen_dispatch)
    mapping = frozen_dispatch["binding_payload"]["canonical_task_mapping"]
    outputs = []
    for task_name, assignment in mapping.items():
        annotator_id = assignment["annotator_id"]
        template = frozen_dispatch["formal"] / assignment["template"].split(
            "formal/agent-review-v1/", 1
        )[1]
        payload = deepcopy(json.loads(template.read_text(encoding="utf-8")))
        for entry in payload["entries"]:
            entry.update(
                {
                    "decision": "include",
                    "boundaries": ["tool"],
                    "trigger": "trigger",
                    "symptom": "symptom",
                    "root_cause": "root cause",
                    "impact": "impact",
                    "evidence_ids": [f"{entry['defect_id']}-fix"],
                    "rationale": "reason",
                }
            )
        payload["completion"] = {
            "completed_at": "2026-08-10T10:00:00+08:00",
            "independent": True,
            "packet_sha256": _sha(frozen_dispatch["packet"]),
        }
        payload["agent_provenance"].update(
            {
                "model_id": "gpt-5.6-sol",
                "prompt_sha256": _sha(frozen_dispatch["prompt"]),
                "artifact_revision": frozen_dispatch["freeze_revision"],
                "started_at": next(
                    item["started_at"]
                    for item in _dispatch_assignments()
                    if item["canonical_task_name"] == task_name
                ),
            }
        )
        path = frozen_dispatch["nano"] / assignment["output"]
        _write_json(path, payload)
        outputs.append(path)
    return record, outputs


def test_agent_review_cli_requires_bound_dispatch_before_analysis(
    frozen_dispatch,
):
    from research.defects.real_corpus_v1.agent_review_cli import main

    record, outputs = _write_complete_review_outputs(frozen_dispatch)
    mapping = frozen_dispatch["binding_payload"]["canonical_task_mapping"]
    analysis_output = frozen_dispatch["formal"] / "analysis-test"
    try:
        assert main(
            [
                "--binding",
                str(frozen_dispatch["binding"]),
                "--dispatch-record",
                str(record),
                "--output",
                str(analysis_output),
            ]
        ) == 0
        assert {
            path.name for path in analysis_output.iterdir()
        } == {"pass_a_agreement.json", "human_audit_packet.json", "SHA256SUMS"}
        for path in analysis_output.iterdir():
            path.unlink()
        analysis_output.rmdir()
        a3_path = frozen_dispatch["nano"] / mapping["formal_a3"]["output"]
        tampered = json.loads(a3_path.read_text(encoding="utf-8"))
        tampered["agent_provenance"]["started_at"] = (
            "2026-08-10T09:04:00+08:00"
        )
        _write_json(a3_path, tampered)
        invalid_output = frozen_dispatch["formal"] / "invalid-analysis-test"
        assert main(
            [
                "--binding",
                str(frozen_dispatch["binding"]),
                "--dispatch-record",
                str(record),
                "--output",
                str(invalid_output),
            ]
        ) == 1
        assert not invalid_output.exists()
    finally:
        for path in outputs:
            if path.exists():
                path.unlink()
        if record.exists():
            record.unlink()
        if analysis_output.exists():
            for path in analysis_output.iterdir():
                path.unlink()
            analysis_output.rmdir()


@pytest.mark.parametrize("failure", ["missing", "wrong_hash"])
def test_postfreeze_cli_rejects_patch_failure_before_writing(
    frozen_dispatch,
    monkeypatch,
    capsys,
    failure,
):
    from research.defects.real_corpus_v1 import postfreeze_analysis

    record, outputs = _write_complete_review_outputs(frozen_dispatch)
    output = frozen_dispatch["formal"] / f"postfreeze-{failure}"
    original = postfreeze_analysis._selected_patch_payloads

    def broken_patch_payloads(*args, **kwargs):
        payloads = original(*args, **kwargs)
        defect_id = sorted(payloads)[0]
        if failure == "missing":
            payloads.pop(defect_id)
        else:
            payloads[defect_id] = b"wrong patch bytes"
        return payloads

    monkeypatch.setattr(
        postfreeze_analysis,
        "_selected_patch_payloads",
        broken_patch_payloads,
    )
    try:
        assert postfreeze_analysis.main(
            [
                "--binding",
                str(frozen_dispatch["binding"]),
                "--dispatch-record",
                str(record),
                "--output",
                str(output),
            ]
        ) == 1
        expected = (
            "patch payloads are required"
            if failure == "missing"
            else "patch SHA-256 mismatch"
        )
        assert expected in capsys.readouterr().err
        assert not output.exists()
    finally:
        for path in outputs:
            if path.exists():
                path.unlink()
        if record.exists():
            record.unlink()


def test_postfreeze_cli_rejects_invalid_raw_before_writing(
    frozen_dispatch,
    capsys,
):
    from research.defects.real_corpus_v1 import postfreeze_analysis

    record, outputs = _write_complete_review_outputs(frozen_dispatch)
    mapping = frozen_dispatch["binding_payload"]["canonical_task_mapping"]
    a3_path = frozen_dispatch["nano"] / mapping["formal_a3"]["output"]
    invalid = json.loads(a3_path.read_text(encoding="utf-8"))
    invalid["completion"]["independent"] = "true"
    _write_json(a3_path, invalid)
    output = frozen_dispatch["formal"] / "postfreeze-invalid-raw"
    try:
        assert postfreeze_analysis.main(
            [
                "--binding",
                str(frozen_dispatch["binding"]),
                "--dispatch-record",
                str(record),
                "--output",
                str(output),
            ]
        ) == 1
        assert "non-timestamp metadata differs" in capsys.readouterr().err
        assert not output.exists()
    finally:
        for path in outputs:
            if path.exists():
                path.unlink()
        if record.exists():
            record.unlink()


def test_postfreeze_cli_rejects_existing_output_before_writing(
    frozen_dispatch,
    capsys,
):
    from research.defects.real_corpus_v1 import postfreeze_analysis

    record, outputs = _write_complete_review_outputs(frozen_dispatch)
    output = frozen_dispatch["formal"] / "postfreeze-existing"
    output.mkdir()
    try:
        assert postfreeze_analysis.main(
            [
                "--binding",
                str(frozen_dispatch["binding"]),
                "--dispatch-record",
                str(record),
                "--output",
                str(output),
            ]
        ) == 1
        assert "output directory must not already exist" in capsys.readouterr().err
        assert not list(output.iterdir())
    finally:
        output.rmdir()
        for path in outputs:
            if path.exists():
                path.unlink()
        if record.exists():
            record.unlink()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda item: item.update(coder_id="A2"), "coder identity"),
        (
            lambda item: item["agent_provenance"].update(model_id="wrong"),
            "frozen model",
        ),
        (
            lambda item: item["agent_provenance"].update(prompt_sha256="0" * 64),
            "prompt digest",
        ),
        (
            lambda item: item["agent_provenance"].update(artifact_revision="a" * 40),
            "artifact revision",
        ),
        (
            lambda item: item["agent_provenance"].update(
                started_at="2026-08-10T09:02:00+08:00"
            ),
            "dispatch assignment started_at",
        ),
        (
            lambda item: item["agent_provenance"].update(
                started_at="2026-08-10T01:01:00Z"
            ),
            "dispatch assignment started_at",
        ),
        (
            lambda item: item["completion"].update(
                completed_at="2026-08-10T09:00:59+08:00"
            ),
            "completion timestamp",
        ),
        (lambda item: item.update(manual_version="1.0"), "manual version"),
        (lambda item: item["entries"].reverse(), "template entry order"),
        (
            lambda item: item["entries"][0].update(evidence_ids=["not-in-packet"]),
            "evidence IDs",
        ),
        (
            lambda item: item["entries"][0].update(operator_ids=["operator"]),
            "operator_ids",
        ),
        (
            lambda item: item["entries"][0].update(trigger=""),
            "include requires trigger",
        ),
        (
            lambda item: item["entries"][0].update(exclusion_reason="not empty"),
            "include requires empty exclusion_reason",
        ),
        (
            lambda item: item["entries"][0].update(
                decision="exclude",
                boundaries=[],
                trigger="",
                symptom="",
                root_cause="",
                impact="",
                exclusion_reason="",
            ),
            "exclude requires exclusion_reason",
        ),
        (
            lambda item: item["entries"][0].update(
                decision="uncertain", exclusion_reason="reason", boundaries=[]
            ),
            "uncertain requires empty exclusion_reason",
        ),
        (
            lambda item: item["entries"][0].update(
                decision="uncertain", exclusion_reason="", boundaries=["tool"]
            ),
            "uncertain requires empty boundaries",
        ),
        (
            lambda item: item["entries"][0].update(
                evidence_ids=[
                    item["entries"][0]["evidence_ids"][0],
                    item["entries"][0]["evidence_ids"][0],
                ]
            ),
            "duplicate evidence_ids",
        ),
        (
            lambda item: item["entries"][0].update(boundaries=["tool", "tool"]),
            "duplicate boundaries",
        ),
        (lambda item: item.update(hidden_partition="leak"), "unexpected field"),
    ],
)
def test_frozen_submission_validation_rejects_unbound_values(
    frozen_dispatch,
    mutation,
    message,
):
    from research.defects.real_corpus_v1.agent_dispatch import (
        validate_frozen_submission,
    )

    record = _create_test_dispatch_record(frozen_dispatch)
    payload = deepcopy(frozen_dispatch["submission_payload"])
    mutation(payload)
    _write_json(frozen_dispatch["submission"], payload)

    try:
        with pytest.raises(ValueError, match=message):
            validate_frozen_submission(
                binding_path=frozen_dispatch["binding"],
                dispatch_record_path=record,
                protocol_path=frozen_dispatch["protocol"],
                expected_annotator="A1",
                template_path=frozen_dispatch["template"],
                submission_path=frozen_dispatch["submission"],
                **_validation_identity(frozen_dispatch, record),
            )
    finally:
        frozen_dispatch["submission"].unlink()
        record.unlink()


@pytest.mark.parametrize("decision", ["exclude", "uncertain"])
def test_frozen_submission_accepts_supported_narrative_for_noninclude(
    frozen_dispatch, decision
):
    from research.defects.real_corpus_v1.agent_dispatch import (
        validate_frozen_submission,
    )

    record = _create_test_dispatch_record(frozen_dispatch)
    payload = deepcopy(frozen_dispatch["submission_payload"])
    payload["entries"][0].update(
        decision=decision,
        exclusion_reason="reason" if decision == "exclude" else "",
        boundaries=[],
        trigger="supported trigger",
        symptom="supported symptom",
        root_cause="supported root cause",
        impact="supported impact",
    )
    _write_json(frozen_dispatch["submission"], payload)
    try:
        assert validate_frozen_submission(
            binding_path=frozen_dispatch["binding"],
            dispatch_record_path=record,
            protocol_path=frozen_dispatch["protocol"],
            expected_annotator="A1",
            template_path=frozen_dispatch["template"],
            submission_path=frozen_dispatch["submission"],
            **_validation_identity(frozen_dispatch, record),
        )["valid"] is True
    finally:
        frozen_dispatch["submission"].unlink()
        record.unlink()


@pytest.mark.parametrize(
    "completed_at",
    ["2026-08-10T09:01:00+08:00", "2026-08-10T02:00:00Z"],
)
def test_frozen_submission_accepts_equal_or_later_completion(
    frozen_dispatch, completed_at
):
    from research.defects.real_corpus_v1.agent_dispatch import (
        validate_frozen_submission,
    )

    record = _create_test_dispatch_record(frozen_dispatch)
    payload = deepcopy(frozen_dispatch["submission_payload"])
    payload["completion"]["completed_at"] = completed_at
    _write_json(frozen_dispatch["submission"], payload)
    try:
        assert validate_frozen_submission(
            binding_path=frozen_dispatch["binding"],
            dispatch_record_path=record,
            protocol_path=frozen_dispatch["protocol"],
            expected_annotator="A1",
            template_path=frozen_dispatch["template"],
            submission_path=frozen_dispatch["submission"],
            **_validation_identity(frozen_dispatch, record),
        )["valid"] is True
    finally:
        frozen_dispatch["submission"].unlink()
        record.unlink()
