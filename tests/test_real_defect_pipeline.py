import hashlib
import json
import os
import subprocess
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

    assert normalized[0] == normalized[1] == normalized[2]
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


def test_agent_review_protocol_freezes_only_known_pre_dispatch_values():
    root = (
        __import__("pathlib").Path(__file__).parents[1]
        / "research"
        / "defects"
        / "real_corpus_v1"
    )
    protocol = json.loads(
        (root / "formal" / "agent-review-v1" / "protocol.json").read_text(
            encoding="utf-8"
        )
    )

    assert protocol == {
        "schema_version": 1,
        "protocol_id": "agent-review-v1",
        "annotator_ids": ["A1", "A2", "A3"],
        "manual_version": "2.0",
        "packet_sha256": (
            "92fc19ca1f96e183dfb21087aae966445ce46e3ae75e81dbdbacfa1f74f82593"
        ),
        "audit_namespace": "agent-defect-human-audit-v1",
        "audit_fraction": 0.1,
        "formal_prior_outputs_excluded": ["H2-CODEX", "machine_precode"],
        "dispatch_status": "unfrozen",
        "dispatch_time_provenance": {
            field: None
            for field in (
                "model_id",
                "prompt_sha256",
                "paper_revision",
                "nanoharness_revision",
                "started_at",
            )
        },
    }


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
        path = root / relative
        assert path.is_file(), relative
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, relative


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
