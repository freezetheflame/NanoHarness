import hashlib
import json
import os
import subprocess
import sys
from collections import Counter

import pytest

from research.defects.real_corpus_v1.build_packets import build_packets
from research.defects.real_corpus_v1.build_candidate_index import (
    build_candidate_index,
)
from research.defects.real_corpus_v1.collect_git_history import (
    collect_fixing_commits,
)
from research.defects.real_corpus_v1.enrich_candidates import enrich_candidates
from research.defects.real_corpus_v1.archive_search_pages import (
    archive_search_pages,
)
from research.defects.real_corpus_v1.machine_precode import machine_precode
from research.defects.real_corpus_v1.build_human_package import build_package
from research.defects.real_corpus_v1.pipeline import (
    blind_packet,
    canonical_candidate_key,
    select_and_partition,
    sha256_file,
)
from research.defects.real_corpus_v1.retrieve import (
    GitHubRetrievalError,
    fetch_pages,
    parse_next_link,
    run_retrieval,
    write_report,
)


def _candidate(repository, number, *, kind="pull"):
    return {
        "repository": repository,
        "canonical_locator": f"https://github.com/{repository}/{kind}/{number}",
        "title": f"Candidate {number}",
        "evidence": [{"evidence_id": f"e-{number}"}],
    }


def test_canonical_key_normalizes_repository_but_preserves_locator():
    candidate = _candidate("Owner/Runtime", 7)

    assert canonical_candidate_key(candidate) == (
        "owner/runtime:https://github.com/Owner/Runtime/pull/7"
    )


def test_selection_is_repository_stratified_and_partition_is_blinded():
    candidates = [
        _candidate("owner/a", index) for index in range(30)
    ] + [
        _candidate("owner/b", index, kind="issues") for index in range(4)
    ]

    selected = select_and_partition(candidates, cap_per_repository=25)

    assert Counter(item["repository"] for item in selected) == {
        "owner/a": 25,
        "owner/b": 4,
    }
    assert sum(
        item["partition"] == "derivation"
        for item in selected
        if item["repository"] == "owner/a"
    ) == 15
    assert sum(
        item["partition"] == "derivation"
        for item in selected
        if item["repository"] == "owner/b"
    ) == 3

    packet = blind_packet(selected)

    assert all("partition" not in item for item in packet)
    assert all("selection_rank" not in item for item in packet)
    assert all("machine_precode" not in item for item in packet)
    assert all("operator_ids" not in item for item in packet)


def test_selection_is_input_order_independent_and_rejects_duplicate_keys():
    candidates = [_candidate("owner/a", index) for index in range(8)]

    assert select_and_partition(candidates) == select_and_partition(
        list(reversed(candidates))
    )

    with pytest.raises(ValueError, match="duplicate canonical candidate keys"):
        select_and_partition(candidates + [dict(candidates[0])])


def test_stable_defect_ids_do_not_expose_partitions():
    selected = select_and_partition([_candidate("owner/a", 1)])

    assert selected[0]["defect_id"].startswith("OWNER-A-")
    assert "DERIVATION" not in selected[0]["defect_id"]
    assert "VALIDATION" not in selected[0]["defect_id"]


def test_sha256_file_hashes_exact_bytes(tmp_path):
    path = tmp_path / "artifact.json"
    payload = json.dumps({"value": "证据"}, ensure_ascii=False).encode("utf-8")
    path.write_bytes(payload)

    assert sha256_file(path) == hashlib.sha256(payload).hexdigest()


class _Response:
    def __init__(self, payload, *, link=None, status=200):
        self.payload = json.dumps(payload).encode("utf-8")
        self.headers = {"Link": link} if link else {}
        self.status = status

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _Transport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    def __call__(self, request):
        self.urls.append(request.full_url)
        return self.responses.pop(0)


def test_parse_next_link_selects_only_next_relation():
    value = (
        '<https://api.test/p1>; rel="prev", '
        '<https://api.test/p3>; rel="next"'
    )

    assert parse_next_link(value) == "https://api.test/p3"
    assert parse_next_link(None) is None


def test_fetch_pages_follows_links_and_retains_raw_payloads(tmp_path):
    transport = _Transport([
        _Response(
            {"items": [{"id": 1}]},
            link='<https://api.test/p2>; rel="next"',
        ),
        _Response({"items": [{"id": 2}]}),
    ])

    items = fetch_pages(
        "https://api.test/p1",
        tmp_path,
        transport=transport,
    )

    assert [item["id"] for item in items] == [1, 2]
    assert transport.urls == ["https://api.test/p1", "https://api.test/p2"]
    assert json.loads((tmp_path / "page-0001.json").read_text())["items"] == [
        {"id": 1}
    ]
    assert json.loads((tmp_path / "page-0002.json").read_text())["items"] == [
        {"id": 2}
    ]


def test_fetch_pages_rejects_non_object_search_payload(tmp_path):
    with pytest.raises(GitHubRetrievalError, match="object with an items list"):
        fetch_pages(
            "https://api.test/p1",
            tmp_path,
            transport=_Transport([_Response([{"id": 1}])]),
        )


def test_run_retrieval_archives_repository_pin_and_query(tmp_path):
    manifest = {
        "manifest_id": "test-manifest",
        "window": {"start": "2024-01-01", "end": "2026-06-30"},
        "repositories": ["owner/runtime"],
        "queries": [
            {
                "id": "closed-bugs",
                "endpoint": "search/issues",
                "query": (
                    "repo:{repository} is:issue is:closed "
                    "closed:{start}..{end} label:bug"
                ),
            }
        ],
    }
    transport = _Transport([
        _Response({"id": 42, "default_branch": "main"}),
        _Response([{"sha": "f" * 40}]),
        _Response({"total_count": 1, "items": [{"id": 7}]}),
    ])

    report = run_retrieval(manifest, tmp_path, transport=transport)

    assert report["complete"] is True
    assert report["repositories"][0]["repository_id"] == 42
    assert report["repositories"][0]["pinned_commit"] == "f" * 40
    assert report["repositories"][0]["queries"][0]["item_count"] == 1
    assert (tmp_path / "owner__runtime" / "repository.json").exists()
    assert (
        tmp_path / "owner__runtime" / "queries" / "closed-bugs" / "page-0001.json"
    ).exists()


def test_run_retrieval_can_resume_one_declared_repository(tmp_path):
    manifest = {
        "manifest_id": "test-manifest",
        "window": {"start": "2024-01-01", "end": "2026-06-30"},
        "repositories": ["owner/first", "owner/second"],
        "queries": [],
    }
    transport = _Transport([
        _Response({"id": 2, "default_branch": "main"}),
        _Response([{"sha": "e" * 40}]),
    ])

    report = run_retrieval(
        manifest,
        tmp_path,
        repositories=["owner/second"],
        transport=transport,
    )

    assert [item["repository"] for item in report["repositories"]] == [
        "owner/second"
    ]
    assert all("owner/first" not in url for url in transport.urls)


def test_run_retrieval_can_resume_one_declared_query(tmp_path):
    manifest = {
        "manifest_id": "test-manifest",
        "window": {"start": "2024-01-01", "end": "2026-06-30"},
        "repositories": ["owner/runtime"],
        "queries": [
            {"id": "first", "endpoint": "search/issues", "query": "first"},
            {"id": "second", "endpoint": "search/issues", "query": "second"},
        ],
    }
    transport = _Transport([
        _Response({"id": 2, "default_branch": "main"}),
        _Response([{"sha": "d" * 40}]),
        _Response({"total_count": 0, "items": []}),
    ])

    report = run_retrieval(
        manifest,
        tmp_path,
        repositories=["owner/runtime"],
        query_ids=["second"],
        transport=transport,
    )

    assert [item["query_id"] for item in report["repositories"][0]["queries"]] == [
        "second"
    ]
    assert "q=second" in transport.urls[-1]


def test_write_report_creates_nested_parent_directory(tmp_path):
    path = tmp_path / "repository" / "reports" / "query.json"

    write_report(path, {"complete": True})

    assert json.loads(path.read_text(encoding="utf-8")) == {"complete": True}


def test_build_packets_creates_identical_blind_coder_templates(tmp_path):
    selected = select_and_partition([
        _candidate("owner/runtime", 1),
        _candidate("owner/runtime", 2),
    ])

    outputs = build_packets(selected, tmp_path)

    packet_text = outputs.evidence_packet.read_text(encoding="utf-8")
    h1 = json.loads(outputs.h1_pass_a.read_text(encoding="utf-8"))
    h2 = json.loads(outputs.h2_pass_a.read_text(encoding="utf-8"))
    assert h1["packet_sha256"] == sha256_file(outputs.evidence_packet)
    assert h2["packet_sha256"] == h1["packet_sha256"]
    h1["coder_id"] = "CODER"
    h2["coder_id"] = "CODER"
    assert h1 == h2
    assert "partition" not in packet_text
    assert "selection_rank" not in packet_text
    assert "operator_ids" not in packet_text
    assert "machine_precode" not in packet_text
    assert tuple(outputs.agent_pass_a) == ("A1", "A2", "A3")
    assert all(path.is_file() for path in outputs.agent_pass_a.values())


@pytest.mark.parametrize(
    "agent_annotators",
    [
        ("A1", "A2", "A3", "A4"),
        ("A1", "A2", "A2"),
        ("A1", "A2", "../escape"),
        ("A3", "A2", "A1"),
        (),
    ],
)
def test_build_packets_rejects_noncanonical_agent_annotators_before_writing(
    tmp_path,
    agent_annotators,
):
    output_dir = tmp_path / "output"
    selected = select_and_partition([_candidate("owner/runtime", 1)])

    with pytest.raises(ValueError, match="exactly A1, A2, A3"):
        build_packets(
            selected,
            output_dir,
            agent_annotators=agent_annotators,
        )

    assert not output_dir.exists()


def test_build_packets_creates_three_structurally_identical_agent_templates(
    tmp_path,
):
    selected = select_and_partition([
        _candidate("owner/runtime", 1),
        _candidate("owner/runtime", 2),
    ])

    baseline_outputs = build_packets(selected, tmp_path)
    legacy_bytes = {
        "evidence_packet": baseline_outputs.evidence_packet.read_bytes(),
        "h1_pass_a": baseline_outputs.h1_pass_a.read_bytes(),
        "h2_pass_a": baseline_outputs.h2_pass_a.read_bytes(),
    }
    outputs = build_packets(
        selected,
        tmp_path,
        agent_annotators=("A1", "A2", "A3"),
    )

    templates = [
        json.loads(outputs.agent_pass_a[annotator_id].read_text(encoding="utf-8"))
        for annotator_id in ("A1", "A2", "A3")
    ]
    normalized = []
    normalized_bytes = []
    for template in templates:
        annotator_id = template["coder_id"]
        assert template["agent_provenance"]["annotator_id"] == annotator_id
        assert template["manual_version"] == "2.0"
        assert template["packet_sha256"] == sha256_file(outputs.evidence_packet)
        assert template["agent_provenance"] == {
            "protocol_id": "agent-review-v1",
            "annotator_id": annotator_id,
            "model_id": None,
            "prompt_sha256": None,
            "input_sha256": template["packet_sha256"],
            "artifact_revision": None,
            "started_at": None,
        }
        assert template["completion"] is None
        assert all(entry["operator_ids"] == [] for entry in template["entries"])
        serialized = json.dumps(template)
        assert "partition" not in serialized
        assert "selection_rank" not in serialized
        assert "machine_precode" not in serialized
        template["coder_id"] = "ANNOTATOR"
        template["agent_provenance"]["annotator_id"] = "ANNOTATOR"
        normalized.append(template)
        normalized_bytes.append(
            (json.dumps(template, indent=2, sort_keys=True) + "\n").encode("utf-8")
        )

    assert normalized[0] == normalized[1] == normalized[2]
    assert normalized_bytes[0] == normalized_bytes[1] == normalized_bytes[2]
    assert outputs.agent_pass_a == {
        annotator_id: (
            tmp_path
            / "formal"
            / "agent-review-v1"
            / "templates"
            / annotator_id
            / "pass_a.json"
        )
        for annotator_id in ("A1", "A2", "A3")
    }
    assert outputs.evidence_packet.read_bytes() == legacy_bytes["evidence_packet"]
    assert outputs.h1_pass_a.read_bytes() == legacy_bytes["h1_pass_a"]
    assert outputs.h2_pass_a.read_bytes() == legacy_bytes["h2_pass_a"]

    repeated = build_packets(
        selected,
        tmp_path / "repeated",
        agent_annotators=("A1", "A2", "A3"),
    )
    assert all(
        outputs.agent_pass_a[annotator_id].read_bytes()
        == repeated.agent_pass_a[annotator_id].read_bytes()
        for annotator_id in ("A1", "A2", "A3")
    )


def test_agent_review_protocol_freezes_identical_dispatch_prompt_and_provenance():
    import hashlib
    from datetime import datetime

    root = (
        __import__("pathlib").Path(__file__).parents[1]
        / "research"
        / "defects"
        / "real_corpus_v1"
    )
    formal_root = root / "formal" / "agent-review-v1"
    protocol = json.loads(
        (formal_root / "protocol.json").read_text(encoding="utf-8")
    )
    prompt_bytes = (formal_root / "AGENT_PROMPT.md").read_bytes()
    prompt_text = prompt_bytes.decode("utf-8")
    prompt_sha256 = hashlib.sha256(prompt_bytes).hexdigest()

    assert protocol["schema_version"] == 1
    assert protocol["protocol_id"] == "agent-review-v1"
    assert protocol["annotator_ids"] == ["A1", "A2", "A3"]
    assert protocol["manual_version"] == "2.0"
    assert protocol["packet_sha256"] == (
        "92fc19ca1f96e183dfb21087aae966445ce46e3ae75e81dbdbacfa1f74f82593"
    )
    assert protocol["audit_namespace"] == "agent-defect-human-audit-v1"
    assert protocol["audit_fraction"] == 0.1
    assert protocol["dispatch_status"] == "frozen_ready"

    provenance = protocol["dispatch_time_provenance"]
    assert provenance == {
        "model_id": "gpt-5.6-sol",
        "model_configuration": {"reasoning_effort": "high"},
        "nanoharness_revision": (
            "8ea573b71ba8c7b7016a4436cec5d304292d44ee"
        ),
        "nanoharness_revision_kind": "pre-freeze_code_and_artifact_revision",
        "paper_revision": "91674e63aab0cf9da599ae52f341cf99004ddd4a",
        "prompt_sha256": prompt_sha256,
        "started_at": provenance["started_at"],
    }
    assert datetime.fromisoformat(provenance["started_at"]).utcoffset() is not None

    dispatch = protocol["dispatch_prompt"]
    assert dispatch["path"] == (
        "research/defects/real_corpus_v1/formal/agent-review-v1/AGENT_PROMPT.md"
    )
    assert dispatch["identical_prompt_bytes_required"] is True
    assert dispatch["protocol_bytes_immutable_for_all_annotators"] is True
    assert dispatch["canonical_task_mapping"] == {
        "formal_a1": {
            "annotator_id": "A1",
            "template": "templates/A1/pass_a.json",
            "output": "pass_a/A1.json",
        },
        "formal_a2": {
            "annotator_id": "A2",
            "template": "templates/A2/pass_a.json",
            "output": "pass_a/A2.json",
        },
        "formal_a3": {
            "annotator_id": "A3",
            "template": "templates/A3/pass_a.json",
            "output": "pass_a/A3.json",
        },
    }
    assert protocol["input_revisions"]["packet"]["sha256"] == protocol["packet_sha256"]
    assert protocol["input_revisions"]["manual"]["version"] == "2.0"
    assert (
        protocol["input_revisions"]["manual"]["paper_revision"]
        == provenance["paper_revision"]
    )
    assert protocol["input_revisions"]["protocol"] == {
        "protocol_id": "agent-review-v1",
        "schema_version": 1,
        "dispatch_status": "frozen_ready",
    }

    for task_name, annotator_id, output in (
        ("formal_a1", "A1", "pass_a/A1.json"),
        ("formal_a2", "A2", "pass_a/A2.json"),
        ("formal_a3", "A3", "pass_a/A3.json"),
    ):
        assert task_name in prompt_text
        assert annotator_id in prompt_text
        assert output in prompt_text
    assert "unknown canonical task name" in prompt_text.lower()
    assert "Do not embed or recompute a prompt hash from this file" in prompt_text
    assert prompt_sha256 not in prompt_text
    assert "77" in prompt_text
    assert "operator_ids" in prompt_text
    assert "independent_started_at" in prompt_text
    assert "completed_at" in prompt_text
    assert "private" in prompt_text.lower()
    assert "machine precode" in prompt_text.lower()


def test_frozen_queries_respect_github_boolean_operator_limit():
    manifest_path = (
        __import__("pathlib").Path(__file__).parents[1]
        / "research"
        / "defects"
        / "real_corpus_v1"
        / "manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert all(
        query["query"].count(" OR ") <= 4
        for query in manifest["queries"]
    )


def test_collect_fixing_commits_uses_pinned_history_and_window(tmp_path):
    repository_path = tmp_path / "subject"
    subprocess.run(["git", "init", str(repository_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repository_path), "config", "user.email", "test@example.test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository_path), "config", "user.name", "Test"],
        check=True,
    )
    commit_environment = os.environ.copy()
    commit_environment.update({
        "GIT_AUTHOR_DATE": "2025-01-02T00:00:00Z",
        "GIT_COMMITTER_DATE": "2025-01-02T00:00:00Z",
    })
    subprocess.run(
        ["git", "-C", str(repository_path), "commit", "--allow-empty", "-m", "feat: add option"],
        check=True,
        env=commit_environment,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repository_path), "commit", "--allow-empty", "-m", "fix: retry duplicate tool call"],
        check=True,
        env=commit_environment,
        capture_output=True,
    )
    pin = subprocess.run(
        ["git", "-C", str(repository_path), "rev-parse", "HEAD"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()

    candidates = collect_fixing_commits(
        repository_path,
        repository="owner/runtime",
        pinned_commit=pin,
        start="2024-01-01",
        end="2026-06-30",
    )

    assert len(candidates) == 1
    assert candidates[0]["revision"] == pin
    assert candidates[0]["canonical_locator"].endswith(f"/commit/{pin}")
    assert candidates[0]["title"] == "fix: retry duplicate tool call"


def test_build_candidate_index_uses_complete_git_histories(tmp_path):
    raw = tmp_path / "raw"
    repositories = ["owner/a", "owner/b"]
    for repository, count in [("owner/a", 30), ("owner/b", 4)]:
        path = raw / repository.replace("/", "__") / "git_history.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        candidates = [_candidate(repository, index) for index in range(count)]
        path.write_text(
            json.dumps({
                "repository": repository,
                "pinned_commit": str(count) * 40,
                "candidate_count": count,
                "candidates": candidates,
            }),
            encoding="utf-8",
        )
    manifest = {
        "manifest_id": "test",
        "repositories": repositories,
        "cap_per_repository": 25,
    }

    outputs = build_candidate_index(raw, manifest, tmp_path / "output")

    universe = json.loads(outputs.candidate_universe.read_text(encoding="utf-8"))
    selected = json.loads(outputs.selected_private.read_text(encoding="utf-8"))
    assert universe["candidate_count"] == 34
    assert selected["candidate_count"] == 29
    assert Counter(item["repository"] for item in selected["candidates"]) == {
        "owner/a": 25,
        "owner/b": 4,
    }
    assert all("partition" in item for item in selected["candidates"])
    assert outputs.packet.evidence_packet.exists()
    assert tuple(outputs.packet.agent_pass_a) == ("A1", "A2", "A3")
    assert all(path.is_file() for path in outputs.packet.agent_pass_a.values())


@pytest.mark.parametrize("script_name", ["build_packets.py", "build_candidate_index.py"])
def test_nested_pipeline_scripts_support_direct_execution(script_name):
    script = (
        __import__("pathlib").Path(__file__).parents[1]
        / "research"
        / "defects"
        / "real_corpus_v1"
        / script_name
    )

    completed = subprocess.run(
        [__import__("sys").executable, str(script), "--help"],
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_build_packets_cli_regenerates_three_agent_templates(tmp_path):
    script = (
        __import__("pathlib").Path(__file__).parents[1]
        / "research"
        / "defects"
        / "real_corpus_v1"
        / "build_packets.py"
    )
    selected = tmp_path / "selected.json"
    candidates = select_and_partition([_candidate("owner/runtime", 1)])
    selected.write_text(
        json.dumps({"candidates": candidates}),
        encoding="utf-8",
    )
    output_dir = tmp_path / "output"

    completed = subprocess.run(
        [
            __import__("sys").executable,
            str(script),
            "--selected",
            str(selected),
            "--output",
            str(output_dir),
        ],
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert all(
        (
            output_dir
            / "formal"
            / "agent-review-v1"
            / "templates"
            / annotator_id
            / "pass_a.json"
        ).is_file()
        for annotator_id in ("A1", "A2", "A3")
    )


def test_enrich_candidates_archives_commit_evidence_and_patch(tmp_path):
    repository_path = tmp_path / "subject"
    subprocess.run(["git", "init", str(repository_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repository_path), "config", "user.email", "test@example.test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository_path), "config", "user.name", "Test"],
        check=True,
    )
    (repository_path / "runtime.py").write_text("value = 1\n", encoding="utf-8")
    (repository_path / "test_runtime.py").write_text(
        "def test_value():\n    assert True\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "-C", str(repository_path), "add", "."],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repository_path),
            "commit",
            "-m",
            "fix: preserve tool result (#17)",
            "-m",
            "The stale observation previously escaped validation.",
        ],
        check=True,
        capture_output=True,
    )
    revision = subprocess.run(
        ["git", "-C", str(repository_path), "rev-parse", "HEAD"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    selected = [{
        "defect_id": "OWNER-RUNTIME-ABC",
        "repository": "owner/runtime",
        "revision": revision,
        "title": "fix: preserve tool result (#17)",
        "canonical_locator": f"https://github.com/owner/runtime/commit/{revision}",
        "partition": "derivation",
        "selection_rank": "a" * 64,
        "evidence": [],
    }]

    enriched = enrich_candidates(
        selected,
        repository_paths={"owner/runtime": repository_path},
        output_dir=tmp_path / "evidence",
    )

    record = enriched[0]
    assert record["commit_message"].startswith("fix: preserve tool result")
    assert record["changed_files"] == ["runtime.py", "test_runtime.py"]
    assert record["candidate_test_files"] == ["test_runtime.py"]
    assert record["pull_request_locator"] == "https://github.com/owner/runtime/pull/17"
    patch = tmp_path / "evidence" / record["patch_path"]
    assert patch.exists()
    assert record["patch_sha256"] == hashlib.sha256(patch.read_bytes()).hexdigest()


def test_archive_search_pages_is_deterministic_and_indexes_exact_bytes(tmp_path):
    raw = tmp_path / "raw"
    first = raw / "owner__a" / "queries" / "q1" / "page-0001.json"
    second = raw / "owner__b" / "queries" / "q2" / "page-0001.json"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b'{"items":[1]}')
    second.write_bytes(b'{"items":[2]}')

    first_archive = tmp_path / "first.zip"
    second_archive = tmp_path / "second.zip"
    first_index = archive_search_pages(raw, first_archive)
    second_index = archive_search_pages(raw, second_archive)

    assert first_archive.read_bytes() == second_archive.read_bytes()
    assert first_index == second_index
    assert [item["path"] for item in first_index["files"]] == [
        "owner__a/queries/q1/page-0001.json",
        "owner__b/queries/q2/page-0001.json",
    ]
    assert first_index["files"][0]["sha256"] == hashlib.sha256(
        first.read_bytes()
    ).hexdigest()


def test_retained_real_defect_packet_is_blind_and_complete():
    root = (
        __import__("pathlib").Path(__file__).parents[1]
        / "research"
        / "defects"
        / "real_corpus_v1"
    )
    packet_bytes = (root / "evidence_packet.json").read_bytes()
    packet = json.loads(packet_bytes)
    private = json.loads(
        (root / "selected_candidates.private.json").read_text(encoding="utf-8")
    )

    assert len(packet["candidates"]) == 77
    assert len(private["candidates"]) == 77
    assert sum(item["partition"] == "derivation" for item in private["candidates"]) == 47
    assert sum(item["partition"] == "validation" for item in private["candidates"]) == 30
    assert b'"partition"' not in packet_bytes
    assert b'"selection_rank"' not in packet_bytes
    assert b'"operator_ids"' not in packet_bytes
    assert b'"machine_precode"' not in packet_bytes
    assert len(list((root / "patches").glob("*.patch"))) == 77


def test_retained_real_defect_snapshot_checksums_match():
    root = (
        __import__("pathlib").Path(__file__).parents[1]
        / "research"
        / "defects"
        / "real_corpus_v1"
    )
    lines = (root / "SHA256SUMS").read_text(encoding="utf-8").splitlines()

    assert len(lines) == 125
    for line in lines:
        expected, relative = line.split("  ", 1)
        if relative.startswith("formal/agent-review-v1/"):
            continue
        path = root / relative
        assert path.is_file(), relative
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, relative


def test_frozen_agent_input_checksums_are_complete_sorted_and_match_bytes():
    root = (
        __import__("pathlib").Path(__file__).parents[1]
        / "research"
        / "defects"
        / "real_corpus_v1"
        / "formal"
        / "agent-review-v1"
    )
    inventory = root / "SHA256SUMS"
    lines = inventory.read_text(encoding="utf-8").splitlines()
    relative_paths = [line.split("  ", 1)[1] for line in lines]

    expected_paths = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
        and path != inventory
        and not path.relative_to(root).as_posix().startswith("pass_a/")
    )
    assert relative_paths == expected_paths
    assert "AGENT_PROMPT.md" in relative_paths
    assert "protocol.json" in relative_paths
    assert relative_paths == sorted(relative_paths)
    for line in lines:
        expected, relative = line.split("  ", 1)
        assert len(expected) == 64
        assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == expected


def test_machine_precode_is_conservative_and_partition_blind():
    candidates = [
        {
            "defect_id": "DOC-1",
            "title": "fix broken documentation link",
            "commit_message": "fix broken documentation link",
            "changed_files": ["docs/install.md"],
            "candidate_test_files": [],
            "partition": "validation",
            "selection_rank": "a" * 64,
            "evidence": [],
        },
        {
            "defect_id": "TOOL-1",
            "title": "fix duplicate tool retry",
            "commit_message": "fix duplicate tool retry",
            "changed_files": ["runtime/tool_retry.py", "tests/test_tool_retry.py"],
            "candidate_test_files": ["tests/test_tool_retry.py"],
            "partition": "derivation",
            "selection_rank": "b" * 64,
            "evidence": [{"evidence_id": "tool-fix"}],
        },
        {
            "defect_id": "AMB-1",
            "title": "fix initialization",
            "commit_message": "fix initialization",
            "changed_files": ["src/init.py"],
            "candidate_test_files": [],
            "partition": "derivation",
            "selection_rank": "c" * 64,
            "evidence": [],
        },
    ]

    output = machine_precode(candidates)

    assert output[0]["provisional_decision"] == "exclude"
    assert output[0]["provisional_exclusion_reason"] == "doc_or_format_only"
    assert output[1]["provisional_decision"] == "include"
    assert "tool" in output[1]["provisional_boundaries"]
    assert output[2]["provisional_decision"] == "uncertain"
    assert all("partition" not in item for item in output)
    assert all("selection_rank" not in item for item in output)


def test_human_package_is_deterministic_and_rejects_forbidden_material(tmp_path):
    packet = tmp_path / "evidence_packet.json"
    template = tmp_path / "pass_a.json"
    manual = tmp_path / "manual.md"
    packet.write_bytes(b'{"candidates":[]}\n')
    template.write_bytes(b'{"coder_id":"H2"}\n')
    manual.write_bytes(b"# Manual\n")
    entries = {
        "evidence/evidence_packet.json": packet,
        "coding/pass_a.json": template,
        "docs/DEFECT_CODING_MANUAL.md": manual,
    }
    metadata = {
        "coder_id": "H2",
        "packet_sha256": hashlib.sha256(packet.read_bytes()).hexdigest(),
    }

    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    build_package(entries, first, metadata=metadata)
    build_package(entries, second, metadata=metadata)

    assert first.read_bytes() == second.read_bytes()
    import zipfile
    with zipfile.ZipFile(first) as archive:
        names = archive.namelist()
        assert "PACKAGE_METADATA.json" in names
        assert "PACKAGE_SHA256SUMS" in names
        assert not any("private" in name.lower() for name in names)
        assert not any("machine" in name.lower() for name in names)
        assert not any("H1" in name for name in names)

    with pytest.raises(ValueError, match="forbidden blind-coding material"):
        build_package({"private/machine_precode.json": packet}, tmp_path / "bad.zip", metadata=metadata)


def _agent_review_fixture(tmp_path):
    decisions = {
        "D01": ("include", "include", "include"),
        "D02": ("include", "include", "exclude"),
        "D03": ("include", "exclude", "uncertain"),
        "D04": ("uncertain", "uncertain", "uncertain"),
        "D05": ("include", "include", "include"),
        "D06": ("exclude", "exclude", "exclude"),
        "D07": ("include", "include", "include"),
        "D08": ("exclude", "exclude", "exclude"),
        "D09": ("include", "include", "include"),
        "D10": ("exclude", "exclude", "exclude"),
    }
    patch_dir = tmp_path / "patches"
    patch_dir.mkdir()
    for defect_id in decisions:
        (patch_dir / f"{defect_id}.patch").write_bytes(defect_id.encode("utf-8"))
    candidates = [
        {
            "defect_id": defect_id,
            "title": f"Candidate {defect_id}",
            "repository": "owner/runtime",
            "canonical_locator": f"https://example.test/{defect_id}",
            "patch_path": f"patches/{defect_id}.patch",
            "patch_sha256": hashlib.sha256(defect_id.encode()).hexdigest(),
            "evidence": [{
                "evidence_id": f"{defect_id}-fix",
                "kind": "fixing_change",
                "locator": f"https://example.test/{defect_id}/fix",
            }],
        }
        for defect_id in decisions
    ]
    packet = tmp_path / "packet.json"
    packet.write_text(
        json.dumps({"schema_version": 1, "candidates": candidates}, sort_keys=True),
        encoding="utf-8",
    )
    packet_digest = hashlib.sha256(packet.read_bytes()).hexdigest()
    annotation_paths = {}
    for agent_index, agent_id in enumerate(("A1", "A2", "A3")):
        entries = []
        for defect_id, agent_decisions in decisions.items():
            decision = agent_decisions[agent_index]
            boundaries = []
            if decision == "include":
                boundaries = [
                    "context" if defect_id == "D09" else "tool"
                ]
            if defect_id == "D05" and agent_id == "A3":
                boundaries = ["context"]
            entries.append({
                "defect_id": defect_id,
                "decision": decision,
                "exclusion_reason": "not in scope" if decision == "exclude" else "",
                "boundaries": boundaries,
                "operator_ids": [],
                "trigger": "trigger",
                "symptom": "symptom",
                "root_cause": "root cause",
                "impact": "impact",
                "evidence_ids": [f"{defect_id}-fix"],
                "rationale": f"{agent_id} reviewed {defect_id}",
            })
        submission = {
            "schema_version": 1,
            "corpus_id": "agent-real-defects-v1",
            "pass_id": "pass_a",
            "coder_id": agent_id,
            "manual_version": "2.0",
            "packet_sha256": packet_digest,
            "entries": entries,
            "completion": {
                "completed_at": "2026-08-10T00:00:00Z",
                "independent": True,
                "packet_sha256": packet_digest,
            },
            "agent_provenance": {
                "protocol_id": "agent-review-v1",
                "annotator_id": agent_id,
                "model_id": "gpt-test",
                "prompt_sha256": "b" * 64,
                "input_sha256": packet_digest,
                "artifact_revision": "abcdef1",
                "started_at": "2026-08-10T00:00:00Z",
            },
        }
        path = tmp_path / f"{agent_id}.json"
        path.write_text(json.dumps(submission), encoding="utf-8")
        annotation_paths[agent_id] = path
    return packet, annotation_paths, candidates, decisions


def _raw_agent_submissions(paths):
    return {agent_id: path.read_bytes() for agent_id, path in paths.items()}


def _agent_patch_payloads(tmp_path, decisions):
    return {
        defect_id: (tmp_path / f"patches/{defect_id}.patch").read_bytes()
        for defect_id in decisions
    }


@pytest.mark.parametrize(
    ("container", "extra_key"),
    [
        ("agent_provenance", "partition"),
        ("completion", "operator_metadata"),
    ],
)
def test_agent_review_rejects_unvalidated_source_submission_extras(
    tmp_path,
    container,
    extra_key,
):
    from research.defects.real_corpus_v1.agent_review import build_review_artifacts

    packet, paths, _, decisions = _agent_review_fixture(tmp_path)
    raw_submissions = _raw_agent_submissions(paths)
    tampered = json.loads(raw_submissions["A3"])
    tampered[container][extra_key] = "must-not-leak"
    raw_submissions["A3"] = json.dumps(tampered).encode("utf-8")

    with pytest.raises(ValueError, match="unexpected field"):
        build_review_artifacts(
            packet.read_bytes(),
            raw_submissions,
            patch_payloads=_agent_patch_payloads(tmp_path, decisions),
        )


@pytest.mark.parametrize(
    ("location", "extra_key"),
    [
        ("submission", "hidden_partition_assignment"),
        ("entry", "operator_metadata"),
    ],
)
def test_agent_review_rejects_unvalidated_submission_structure_extras(
    tmp_path,
    location,
    extra_key,
):
    from research.defects.real_corpus_v1.agent_review import analyze_agent_reviews

    packet, paths, _, _ = _agent_review_fixture(tmp_path)
    raw_submissions = _raw_agent_submissions(paths)
    tampered = json.loads(raw_submissions["A2"])
    target = tampered if location == "submission" else tampered["entries"][0]
    target[extra_key] = "must-not-be-ignored"
    raw_submissions["A2"] = json.dumps(tampered).encode("utf-8")

    with pytest.raises(ValueError, match="unexpected field"):
        analyze_agent_reviews(packet.read_bytes(), raw_submissions)


def test_agent_review_accepts_legal_empty_pass_a_operator_ids(tmp_path):
    from research.defects.real_corpus_v1.agent_review import analyze_agent_reviews

    packet, paths, _, _ = _agent_review_fixture(tmp_path)

    analysis = analyze_agent_reviews(
        packet.read_bytes(),
        _raw_agent_submissions(paths),
    )

    assert analysis["invalid_count"] == 0


def test_agent_review_source_sha_is_bound_to_analyzed_raw_bytes(tmp_path):
    from research.defects.real_corpus_v1.agent_review import build_review_artifacts

    packet, paths, _, decisions = _agent_review_fixture(tmp_path)
    raw_submissions = _raw_agent_submissions(paths)
    raw_submissions["A2"] = b" \n" + raw_submissions["A2"] + b"\n"

    _, human_packet = build_review_artifacts(
        packet.read_bytes(),
        raw_submissions,
        patch_payloads=_agent_patch_payloads(tmp_path, decisions),
    )

    assert human_packet["source_submissions"]["A2"]["sha256"] == hashlib.sha256(
        raw_submissions["A2"]
    ).hexdigest()


@pytest.mark.parametrize("field_name", ["boundaries", "evidence_ids"])
def test_agent_review_rejects_duplicate_entry_multivalue_fields(
    tmp_path,
    field_name,
):
    from research.defects.real_corpus_v1.agent_review import analyze_agent_reviews

    packet, paths, _, _ = _agent_review_fixture(tmp_path)
    raw_submissions = _raw_agent_submissions(paths)
    tampered = json.loads(raw_submissions["A1"])
    tampered["entries"][0][field_name] *= 2
    raw_submissions["A1"] = json.dumps(tampered).encode("utf-8")

    with pytest.raises(ValueError, match=f"duplicate {field_name}"):
        analyze_agent_reviews(packet.read_bytes(), raw_submissions)


def test_agent_review_requires_exact_agents_and_reports_agreement(tmp_path):
    from research.defects.real_corpus_v1.agent_review import analyze_agent_reviews

    packet, paths, _, _ = _agent_review_fixture(tmp_path)
    raw_submissions = _raw_agent_submissions(paths)

    analysis = analyze_agent_reviews(packet.read_bytes(), raw_submissions)

    assert analysis["annotator_ids"] == ["A1", "A2", "A3"]
    assert analysis["pass_id"] == "pass_a"
    assert analysis["sample_size"] == 10
    assert analysis["missing_count"] == 0
    assert analysis["invalid_count"] == 0
    assert analysis["decision_marginals"] == {
        "A1": {"include": 6, "exclude": 3, "uncertain": 1},
        "A2": {"include": 5, "exclude": 4, "uncertain": 1},
        "A3": {"include": 4, "exclude": 4, "uncertain": 2},
    }
    assert analysis["decision_agreement"]["fleiss_denominator"] == 10
    assert analysis["decision_agreement"]["fleiss_kappa"] == pytest.approx(
        0.7211895911
    )
    assert analysis["decision_agreement"]["pairwise_cohen_kappa"] == pytest.approx({
        "A1:A2": 0.8245614035,
        "A1:A3": 0.6774193548,
        "A2:A3": 0.6774193548,
    })
    assert analysis["decision_agreement"]["pairwise_cohen_denominators"] == {
        "A1:A2": 10,
        "A1:A3": 10,
        "A2:A3": 10,
    }
    assert analysis["decision_agreement"]["counts"] == {
        "unanimous": 8,
        "two_to_one": 1,
        "three_way": 1,
        "any_uncertain": 2,
    }
    assert analysis["boundary_agreement"]["pairwise_denominators"] == {
        "A1:A2": 5,
        "A1:A3": 4,
        "A2:A3": 4,
    }
    assert analysis["boundary_agreement"]["pairwise_mean_jaccard"] == pytest.approx({
        "A1:A2": 1.0,
        "A1:A3": 0.75,
        "A2:A3": 0.75,
    })
    assert analysis["boundary_agreement"]["mean_pairwise_jaccard"] == pytest.approx(
        5 / 6
    )

    with pytest.raises(ValueError, match="exactly A1, A2, A3"):
        analyze_agent_reviews(
            packet.read_bytes(),
            {"A1": raw_submissions["A1"], "A2": raw_submissions["A2"]},
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda item: item.update(completion=None), "completion"),
        (lambda item: item.update(corpus_id="different"), "corpus"),
        (lambda item: item.update(schema_version=2), "schema version"),
        (
            lambda item: item["agent_provenance"].update(
                artifact_revision="1234567"
            ),
            "artifact revision",
        ),
        (lambda item: item["entries"][0].update(evidence_ids=[]), "evidence"),
        (
            lambda item: item["entries"][0].update(evidence_ids=["unknown"]),
            "evidence",
        ),
        (lambda item: item["entries"][0].update(boundaries=[]), "boundaries"),
        (lambda item: item["entries"][0].update(trigger=""), "trigger"),
        (lambda item: item["entries"][0].update(symptom=""), "symptom"),
        (lambda item: item["entries"][0].update(root_cause=""), "root_cause"),
        (lambda item: item["entries"][0].update(impact=""), "impact"),
        (
            lambda item: item["entries"][5].update(exclusion_reason=""),
            "exclusion reason",
        ),
        (
            lambda item: item["entries"][5].update(boundaries=["tool"]),
            "boundaries",
        ),
    ],
)
def test_agent_review_rejects_incomplete_or_inconsistent_formal_inputs(
    tmp_path,
    mutation,
    message,
):
    from research.defects.real_corpus_v1.agent_review import analyze_agent_reviews

    packet, paths, _, _ = _agent_review_fixture(tmp_path)
    packet_payload = json.loads(packet.read_text(encoding="utf-8"))
    submissions = {
        agent_id: json.loads(path.read_text(encoding="utf-8"))
        for agent_id, path in paths.items()
    }
    mutation(submissions["A3"])
    raw_submissions = {
        agent_id: json.dumps(submission).encode("utf-8")
        for agent_id, submission in submissions.items()
    }

    with pytest.raises(ValueError, match=message):
        analyze_agent_reviews(packet.read_bytes(), raw_submissions)


def test_agent_review_selects_union_with_reasons_and_is_order_independent(tmp_path):
    from research.defects.real_corpus_v1.agent_review import build_review_artifacts

    packet, paths, _, decisions = _agent_review_fixture(tmp_path)
    packet_payload = json.loads(packet.read_text(encoding="utf-8"))
    submissions = {
        agent_id: json.loads(path.read_text(encoding="utf-8"))
        for agent_id, path in paths.items()
    }
    raw_submissions = _raw_agent_submissions(paths)
    patch_payloads = {
        defect_id: (tmp_path / f"patches/{defect_id}.patch").read_bytes()
        for defect_id in decisions
    }

    first = build_review_artifacts(
        packet.read_bytes(),
        raw_submissions,
        patch_payloads=patch_payloads,
    )
    reversed_submissions = {
        agent_id: {**submission, "entries": list(reversed(submission["entries"]))}
        for agent_id, submission in reversed(list(submissions.items()))
    }
    reversed_raw_submissions = {
        agent_id: json.dumps(submission).encode("utf-8")
        for agent_id, submission in reversed_submissions.items()
    }
    second = build_review_artifacts(
        packet.read_bytes(),
        reversed_raw_submissions,
        patch_payloads=patch_payloads,
    )

    assert first[0] == second[0]
    assert first[1]["candidates"] == second[1]["candidates"]
    agreement, human_packet = first
    selected = {item["defect_id"]: item for item in human_packet["candidates"]}
    assert {"D02", "D03", "D04", "D05"} < set(selected)
    assert len(selected) == 5
    assert agreement["human_audit_selection"]["selected_count"] == 5
    assert agreement["human_audit_selection"]["eligible_unanimous_count"] == 6
    assert agreement["human_audit_selection"]["deterministic_audit_count"] == 1
    assert agreement["human_audit_selection"]["reasons_by_defect_id"]["D02"] == [
        "decision_disagreement",
        "boundary_disagreement",
    ]
    assert "any_uncertain" in agreement["human_audit_selection"][
        "reasons_by_defect_id"
    ]["D03"]
    assert agreement["human_audit_selection"]["reasons_by_defect_id"]["D04"] == [
        "any_uncertain"
    ]
    assert agreement["human_audit_selection"]["reasons_by_defect_id"]["D05"] == [
        "boundary_disagreement"
    ]
    random_ids = [
        defect_id
        for defect_id, reasons in agreement["human_audit_selection"][
            "reasons_by_defect_id"
        ].items()
        if reasons == ["deterministic_consistency_audit"]
    ]
    expected_random = min(
        {"D01", "D06", "D07", "D08", "D09", "D10"},
        key=lambda defect_id: hashlib.sha256(
            f"agent-defect-human-audit-v1|{defect_id}".encode()
        ).hexdigest(),
    )
    assert random_ids == [expected_random]
    serialized = json.dumps(human_packet)
    for forbidden in ("partition", "operator_catalog", "machine_precode", "private"):
        assert forbidden not in serialized
    assert all(set(item) == {
        "defect_id", "candidate", "annotations", "selection_reasons", "human_review"
    } for item in human_packet["candidates"])
    assert all(list(item["annotations"]) == ["A1", "A2", "A3"] for item in selected.values())
    assert all(
        item["candidate"]["patch_text"] == item["defect_id"]
        and item["candidate"]["patch_sha256"]
        == hashlib.sha256(item["defect_id"].encode()).hexdigest()
        for item in selected.values()
    )
    assert all(
        "operator_ids" not in annotation
        for item in selected.values()
        for annotation in item["annotations"].values()
    )
    assert all(item["human_review"] == {
        "reviewer_id": None,
        "reviewed_at": None,
        "evidence_considered": [],
        "disposition": None,
        "final_decision": None,
        "final_exclusion_reason": "",
        "final_boundaries": [],
        "final_trigger": "",
        "final_symptom": "",
        "final_root_cause": "",
        "final_impact": "",
        "final_rationale": "",
    } for item in selected.values())
    assert human_packet["source_submissions"] == {
        agent_id: {
            "sha256": hashlib.sha256(raw_submissions[agent_id]).hexdigest(),
            "provenance": submissions[agent_id]["agent_provenance"],
            "completion": submissions[agent_id]["completion"],
        }
        for agent_id in ("A1", "A2", "A3")
    }


@pytest.mark.parametrize(
    "forbidden_key",
    [
        "derived_partition_hint",
        "operator_metadata",
        "is_private_data",
        "machine_hint",
        "candidate_selection_rank_note",
        "hidden_selection_bucket",
        "selection_metadata",
    ],
)
def test_agent_review_rejects_forbidden_human_packet_material(
    tmp_path,
    forbidden_key,
):
    from research.defects.real_corpus_v1.agent_review import build_review_artifacts

    packet, paths, _, _ = _agent_review_fixture(tmp_path)
    packet_payload = json.loads(packet.read_text(encoding="utf-8"))
    packet_payload["candidates"][0]["nested"] = {
        forbidden_key: "must-not-leak"
    }
    submissions = {
        agent_id: json.loads(path.read_text(encoding="utf-8"))
        for agent_id, path in paths.items()
    }

    forbidden_packet_bytes = json.dumps(packet_payload).encode("utf-8")
    with pytest.raises(ValueError, match="forbidden blind packet material"):
        build_review_artifacts(
            forbidden_packet_bytes,
            _raw_agent_submissions(paths),
        )


def test_agent_review_cli_writes_deterministic_artifacts_and_checksums(tmp_path):
    packet, paths, _, _ = _agent_review_fixture(tmp_path)
    script = (
        __import__("pathlib").Path(__file__).parents[1]
        / "research" / "defects" / "real_corpus_v1" / "agent_review_cli.py"
    )

    outputs = []
    for name in ("first", "second"):
        output = tmp_path / name
        completed = subprocess.run([
            sys.executable,
            str(script),
            "--packet", str(packet),
            "--a1", str(paths["A1"]),
            "--a2", str(paths["A2"]),
            "--a3", str(paths["A3"]),
            "--output", str(output),
        ], text=True, capture_output=True)
        assert completed.returncode == 0, completed.stderr
        outputs.append(output)

    expected_names = {
        "pass_a_agreement.json", "human_audit_packet.json", "SHA256SUMS"
    }
    assert {path.name for path in outputs[0].iterdir()} == expected_names
    assert all(
        (outputs[0] / name).read_bytes() == (outputs[1] / name).read_bytes()
        for name in expected_names
    )
    checksum_lines = (outputs[0] / "SHA256SUMS").read_text().splitlines()
    assert checksum_lines == [
        f"{hashlib.sha256((outputs[0] / name).read_bytes()).hexdigest()}  {name}"
        for name in ("human_audit_packet.json", "pass_a_agreement.json")
    ]
    human_packet = json.loads(
        (outputs[0] / "human_audit_packet.json").read_text(encoding="utf-8")
    )
    assert all(
        item["candidate"]["patch_text"] == item["defect_id"]
        for item in human_packet["candidates"]
    )
    assert human_packet["source_submissions"] == {
        agent_id: {
            "sha256": hashlib.sha256(paths[agent_id].read_bytes()).hexdigest(),
            "provenance": json.loads(paths[agent_id].read_text())["agent_provenance"],
            "completion": json.loads(paths[agent_id].read_text())["completion"],
        }
        for agent_id in ("A1", "A2", "A3")
    }


@pytest.mark.parametrize("failure", ["missing", "wrong_hash"])
def test_agent_review_cli_rejects_missing_or_mismatched_selected_patch(
    tmp_path,
    failure,
):
    packet, paths, _, _ = _agent_review_fixture(tmp_path)
    script = (
        __import__("pathlib").Path(__file__).parents[1]
        / "research" / "defects" / "real_corpus_v1" / "agent_review_cli.py"
    )
    packet_payload = json.loads(packet.read_text(encoding="utf-8"))
    if failure == "missing":
        packet_payload["candidates"][1].pop("patch_path")
    else:
        packet_payload["candidates"][1]["patch_sha256"] = "0" * 64
    packet.write_text(json.dumps(packet_payload, sort_keys=True), encoding="utf-8")
    new_digest = hashlib.sha256(packet.read_bytes()).hexdigest()
    for path in paths.values():
        submission = json.loads(path.read_text(encoding="utf-8"))
        submission["packet_sha256"] = new_digest
        submission["agent_provenance"]["input_sha256"] = new_digest
        submission["completion"]["packet_sha256"] = new_digest
        path.write_text(json.dumps(submission), encoding="utf-8")
    output = tmp_path / "output"

    completed = subprocess.run([
        sys.executable, str(script),
        "--packet", str(packet), "--a1", str(paths["A1"]),
        "--a2", str(paths["A2"]), "--a3", str(paths["A3"]),
        "--output", str(output),
    ], text=True, capture_output=True)

    assert completed.returncode != 0
    assert not output.exists()


@pytest.mark.parametrize(
    ("field_name", "invalid_value", "provenance_field"),
    [
        ("manual_version", "3.0", False),
        ("protocol_id", "agent-review-v2", True),
        ("model_id", "different-model", True),
        ("prompt_sha256", "C" * 64, True),
    ],
)
def test_agent_review_cli_rejects_invalid_inputs_before_writing(
    tmp_path,
    field_name,
    invalid_value,
    provenance_field,
):
    packet, paths, _, _ = _agent_review_fixture(tmp_path)
    script = (
        __import__("pathlib").Path(__file__).parents[1]
        / "research" / "defects" / "real_corpus_v1" / "agent_review_cli.py"
    )
    invalid = json.loads(paths["A3"].read_text(encoding="utf-8"))
    target = invalid["agent_provenance"] if provenance_field else invalid
    target[field_name] = invalid_value
    invalid_path = tmp_path / "invalid-a3.json"
    invalid_path.write_text(json.dumps(invalid), encoding="utf-8")
    output = tmp_path / "invalid-output"

    completed = subprocess.run([
        sys.executable, str(script),
        "--packet", str(packet), "--a1", str(paths["A1"]),
        "--a2", str(paths["A2"]), "--a3", str(invalid_path),
        "--output", str(output),
    ], text=True, capture_output=True)

    assert completed.returncode != 0
    assert not output.exists()

    existing = tmp_path / "existing"
    existing.mkdir()
    completed = subprocess.run([
        sys.executable, str(script),
        "--packet", str(packet), "--a1", str(paths["A1"]),
        "--a2", str(paths["A2"]), "--a3", str(paths["A3"]),
        "--output", str(existing),
    ], text=True, capture_output=True)
    assert completed.returncode != 0


def test_agent_review_cli_rejects_mismatched_input_id_set(tmp_path):
    packet, paths, _, _ = _agent_review_fixture(tmp_path)
    script = (
        __import__("pathlib").Path(__file__).parents[1]
        / "research" / "defects" / "real_corpus_v1" / "agent_review_cli.py"
    )
    mismatched = json.loads(paths["A3"].read_text(encoding="utf-8"))
    mismatched["entries"].pop()
    mismatched_path = tmp_path / "mismatched-a3.json"
    mismatched_path.write_text(json.dumps(mismatched), encoding="utf-8")
    output = tmp_path / "mismatched-output"

    completed = subprocess.run([
        sys.executable, str(script),
        "--packet", str(packet), "--a1", str(paths["A1"]),
        "--a2", str(paths["A2"]), "--a3", str(mismatched_path),
        "--output", str(output),
    ], text=True, capture_output=True)

    assert completed.returncode != 0
    assert not output.exists()
