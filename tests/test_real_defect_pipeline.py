import hashlib
import json
import os
import subprocess
from collections import Counter

import pytest

from research.defects.real_corpus_v1.build_packets import build_packets
from research.defects.real_corpus_v1.collect_git_history import (
    collect_fixing_commits,
)
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
