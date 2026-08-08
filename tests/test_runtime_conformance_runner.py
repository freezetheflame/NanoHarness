import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from nanoharness.testing import RuntimeConformanceReport
from pydantic import ValidationError
from research.pilots.runtime_conformance.run import (
    DEFAULT_MANIFEST,
    RuntimeConformanceManifest,
    run_experiment,
    runtime_conformance_manifest_digest,
)


def _assert_hashes(root: Path) -> None:
    lines = (root / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    assert [line.split("  ", 1)[1] for line in lines] == [
        "manifest.json",
        "raw/conformance_report.json",
        "summary.json",
    ]
    for line in lines:
        expected, relative = line.split("  ", 1)
        assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == expected


def test_runner_writes_eight_cells_four_comparisons_and_hashes(tmp_path):
    report, summary = run_experiment(tmp_path, artifact_revision="c" * 40)

    raw_path = tmp_path / "raw" / "conformance_report.json"
    restored = RuntimeConformanceReport.model_validate_json(
        raw_path.read_text(encoding="utf-8")
    )
    assert restored == report
    assert len(report.cells) == 8
    assert len(report.comparisons) == 4
    assert all(item.passed for item in report.comparisons)
    assert summary["cells"] == 8
    assert summary["comparisons"] == 4
    assert summary["passed"] is True
    assert summary["infrastructure_errors"] == []
    assert b"\r\n" not in raw_path.read_bytes()
    assert b"\r\n" not in (tmp_path / "summary.json").read_bytes()
    assert b"\r\n" not in (tmp_path / "SHA256SUMS").read_bytes()
    _assert_hashes(tmp_path)


def test_runner_refuses_to_overwrite_retained_evidence(tmp_path):
    run_experiment(tmp_path, artifact_revision="c" * 40)

    with pytest.raises(FileExistsError, match="already exists"):
        run_experiment(tmp_path, artifact_revision="c" * 40)


def test_runner_explicit_overwrite_replaces_generated_files(tmp_path):
    run_experiment(tmp_path, artifact_revision="c" * 40)

    report, summary = run_experiment(
        tmp_path,
        artifact_revision="d" * 40,
        overwrite=True,
    )

    assert report.artifact_revision == "d" * 40
    assert summary["artifact_revision"] == "d" * 40
    _assert_hashes(tmp_path)


def test_manifest_rejects_missing_comparison_field():
    payload = json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    payload["comparison_fields"].remove("permission_decisions")
    digest_payload = {key: value for key, value in payload.items() if key != "manifest_digest"}
    payload["manifest_digest"] = runtime_conformance_manifest_digest(digest_payload)

    with pytest.raises(ValidationError, match="comparison fields do not match"):
        RuntimeConformanceManifest.model_validate(payload)


def test_direct_script_cli_runs_from_repository_root(tmp_path):
    script = DEFAULT_MANIFEST.parent / "run.py"

    completed = subprocess.run(
        [sys.executable, str(script), "--output-dir", str(tmp_path)],
        cwd=DEFAULT_MANIFEST.parents[3],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "wrote 8 cells and 4 comparisons; passed=True" in completed.stdout
