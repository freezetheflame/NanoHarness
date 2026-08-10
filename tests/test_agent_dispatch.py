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
    ]
    for path in tooling_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {path.name}\n", encoding="utf-8", newline="\n")
    prompt = formal / "AGENT_PROMPT.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("identical prompt\n", encoding="utf-8", newline="\n")
    packet = corpus / "evidence_packet.json"
    defect_ids = [f"D{index:03d}" for index in range(1, 78)]
    candidates = [
        {
            "defect_id": defect_id,
            "evidence": [{"evidence_id": f"{defect_id}-fix"}],
        }
        for defect_id in defect_ids
    ]
    _write_json(packet, {"candidates": candidates})
    patch_paths = {}
    for defect_id in defect_ids:
        path = corpus / f"patches/{defect_id}.patch"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"patch {defect_id}\n", encoding="utf-8", newline="\n"
        )
        patch_paths[defect_id] = path

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
            "started_at": "2026-08-10T09:00:00+08:00",
        }
    )
    _write_json(submission, submission_payload)

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
        "top_manifest": top_manifest,
    }


def test_preflight_returns_machine_readable_frozen_assignment(frozen_dispatch):
    from research.defects.real_corpus_v1.agent_dispatch import preflight_dispatch

    result = preflight_dispatch(
        binding_path=frozen_dispatch["binding"],
        task_name="/root/formal_a1",
        expected_annotator="A1",
        expected_template=frozen_dispatch["binding_payload"]["canonical_task_mapping"][
            "formal_a1"
        ]["template"],
        expected_output=frozen_dispatch["binding_payload"]["canonical_task_mapping"][
            "formal_a1"
        ]["output"],
        phase="start",
        paper_repo=frozen_dispatch["paper"],
    )

    assert result["valid"] is True
    assert result["assignment"]["annotator_id"] == "A1"
    assert result["binding_sha256"] == _sha(frozen_dispatch["binding"])
    assert result["freeze_payload_revision"] == frozen_dispatch["freeze_revision"]


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
            paper_repo=frozen_dispatch["paper"],
        )


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
    ["prompt", "protocol", "nested", "template", "packet", "patch"],
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


def test_retained_dispatch_binding_preflights_all_assignments():
    from research.defects.real_corpus_v1.agent_dispatch import preflight_dispatch

    repo = Path(__file__).parents[1]
    formal = repo / "research/defects/real_corpus_v1/formal/agent-review-v1"
    binding = formal / "DISPATCH_BINDING.json"
    payload = json.loads(binding.read_text(encoding="utf-8"))
    for suffix, assignment in payload["canonical_task_mapping"].items():
        result = preflight_dispatch(
            binding_path=binding,
            task_name=f"/root/{suffix}",
            expected_annotator=assignment["annotator_id"],
            expected_template=assignment["template"],
            expected_output=assignment["output"],
            phase="start",
        )
        assert result["freeze_payload_revision"] == (
            "0dfaae11c7a35a15d24c711d8888b21a30c7679b"
        )


def test_frozen_submission_validation_accepts_exact_bound_submission(
    frozen_dispatch,
):
    from research.defects.real_corpus_v1.agent_dispatch import (
        validate_frozen_submission,
    )

    result = validate_frozen_submission(
        binding_path=frozen_dispatch["binding"],
        protocol_path=frozen_dispatch["protocol"],
        expected_annotator="A1",
        template_path=frozen_dispatch["template"],
        submission_path=frozen_dispatch["submission"],
    )

    assert result == {"annotator_id": "A1", "entry_count": 77, "valid": True}


def test_dispatch_cli_runs_preflight_and_submission_validation(
    frozen_dispatch,
    capsys,
):
    from research.defects.real_corpus_v1.agent_dispatch_cli import main

    assignment = frozen_dispatch["binding_payload"]["canonical_task_mapping"][
        "formal_a1"
    ]
    exit_code = main(
        [
            "preflight",
            "--binding",
            str(frozen_dispatch["binding"]),
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

    exit_code = main(
        [
            "validate-submission",
            "--binding",
            str(frozen_dispatch["binding"]),
            "--protocol",
            str(frozen_dispatch["protocol"]),
            "--expected-annotator",
            "A1",
            "--template",
            str(frozen_dispatch["template"]),
            "--submission",
            str(frozen_dispatch["submission"]),
        ]
    )
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True


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

    payload = deepcopy(frozen_dispatch["submission_payload"])
    mutation(payload)
    _write_json(frozen_dispatch["submission"], payload)

    try:
        with pytest.raises(ValueError, match=message):
            validate_frozen_submission(
                binding_path=frozen_dispatch["binding"],
                protocol_path=frozen_dispatch["protocol"],
                expected_annotator="A1",
                template_path=frozen_dispatch["template"],
                submission_path=frozen_dispatch["submission"],
            )
    finally:
        _write_json(
            frozen_dispatch["submission"],
            frozen_dispatch["submission_payload"],
        )
