"""Execute and archive the frozen M1--M4 runtime conformance experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, Field, model_validator

from nanoharness.testing import RuntimeConformanceReport
from research.pilots.runtime_conformance.cases import (
    CASE_IDS,
    STOP_REASON_EQUIVALENCES,
    run_case_pair,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_MANIFEST = ROOT / "manifest.json"
GENERATED_PATHS = (
    Path("raw/conformance_report.json"),
    Path("summary.json"),
    Path("SHA256SUMS"),
)
EXPECTED_COMPARISON_FIELDS = (
    "run_status",
    "stop_reason",
    "goal_achieved",
    "report_passed",
    "execution_error_type",
    "tool_attempts",
    "permission_decisions",
    "recovery_observed",
    "lifecycle_start_count",
    "lifecycle_end_count",
    "lifecycle_paired",
    "event_ids_unique",
    "sequences_monotonic",
    "trace_ids_consistent",
    "scenario_round_trip",
    "report_round_trip",
)


class RuntimeConformanceManifest(BaseModel):
    schema_version: int = 1
    experiment_id: str = Field(min_length=1)
    case_ids: List[str]
    runtime_versions: Dict[str, str]
    stop_reason_equivalences: Dict[str, str]
    comparison_fields: List[str]
    frozen_at: datetime
    command: str = Field(min_length=1)
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_manifest(self):
        if self.schema_version != 1:
            raise ValueError("unsupported runtime conformance manifest version")
        if self.case_ids != list(CASE_IDS):
            raise ValueError("manifest must contain ordered M1--M4 cases")
        if self.runtime_versions != {
            "nanoharness": "0.1.0",
            "langgraph": "1.2.10",
        }:
            raise ValueError("manifest runtime versions do not match frozen subjects")
        if self.stop_reason_equivalences != STOP_REASON_EQUIVALENCES:
            raise ValueError("manifest stop-reason equivalences do not match cases")
        if self.comparison_fields != list(EXPECTED_COMPARISON_FIELDS):
            raise ValueError("manifest comparison fields do not match contract")
        if self.frozen_at.utcoffset() is None:
            raise ValueError("manifest frozen_at requires a timezone offset")
        expected = runtime_conformance_manifest_digest(
            self.model_dump(mode="json", exclude={"manifest_digest"})
        )
        if self.manifest_digest != expected:
            raise ValueError("runtime conformance manifest_digest does not match")
        return self


def runtime_conformance_manifest_digest(payload: Dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def run_experiment(
    output_dir: Path | str,
    *,
    manifest_path: Path | str = DEFAULT_MANIFEST,
    artifact_revision: Optional[str] = None,
    overwrite: bool = False,
) -> tuple[RuntimeConformanceReport, Dict[str, Any]]:
    output_root = Path(output_dir).resolve()
    source_manifest = Path(manifest_path).resolve()
    manifest = RuntimeConformanceManifest.model_validate_json(
        source_manifest.read_text(encoding="utf-8")
    )
    _validate_installed_versions(manifest)
    targets = [output_root / relative for relative in GENERATED_PATHS]
    existing = [path for path in targets if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(f"retained evidence already exists: {existing[0]}")

    output_root.mkdir(parents=True, exist_ok=True)
    raw_path = output_root / "raw" / "conformance_report.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    target_manifest = output_root / "manifest.json"
    if target_manifest != source_manifest:
        if target_manifest.exists() and not overwrite:
            raise FileExistsError(
                f"retained evidence already exists: {target_manifest}"
            )
        target_manifest.write_bytes(source_manifest.read_bytes())

    revision = artifact_revision or _git_revision()
    started_at = datetime.now().astimezone()
    pairs = [run_case_pair(case_id) for case_id in manifest.case_ids]
    finished_at = datetime.now().astimezone()
    report = RuntimeConformanceReport(
        experiment_id=manifest.experiment_id,
        manifest_digest=manifest.manifest_digest,
        artifact_revision=revision,
        runtime_versions=manifest.runtime_versions,
        started_at=started_at,
        finished_at=finished_at,
        cells=[
            evidence
            for pair in pairs
            for evidence in (pair.nanoharness, pair.langgraph)
        ],
        comparisons=[pair.comparison for pair in pairs],
        infrastructure_errors=[],
    )
    if not all(item.passed for item in report.comparisons):
        failures = [
            item.case_id for item in report.comparisons if not item.passed
        ]
        raise RuntimeError(f"semantic conformance failed for cases {failures}")
    raw_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")

    summary = {
        "schema_version": 1,
        "experiment_id": manifest.experiment_id,
        "manifest_digest": manifest.manifest_digest,
        "artifact_revision": revision,
        "runtime_versions": manifest.runtime_versions,
        "cells": len(report.cells),
        "comparisons": len(report.comparisons),
        "passed": all(item.passed for item in report.comparisons),
        "infrastructure_errors": report.infrastructure_errors,
        "cases": [
            {
                "case_id": item.case_id,
                "passed": item.passed,
                "mismatches": len(item.mismatches),
            }
            for item in report.comparisons
        ],
    }
    summary_path = output_root / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_hashes(output_root)
    return report, summary


def _validate_installed_versions(manifest: RuntimeConformanceManifest) -> None:
    installed = {
        "nanoharness": metadata.version("nanoharness"),
        "langgraph": metadata.version("langgraph"),
    }
    if installed != manifest.runtime_versions:
        raise RuntimeError(
            f"installed runtime versions {installed} do not match "
            f"manifest {manifest.runtime_versions}"
        )


def _git_revision() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _write_hashes(output_root: Path) -> None:
    relative_paths = (
        Path("manifest.json"),
        Path("raw/conformance_report.json"),
        Path("summary.json"),
    )
    lines = [
        f"{hashlib.sha256((output_root / path).read_bytes()).hexdigest()}  "
        f"{path.as_posix()}"
        for path in relative_paths
    ]
    (output_root / "SHA256SUMS").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=ROOT)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    report, summary = run_experiment(
        args.output_dir,
        manifest_path=args.manifest,
        overwrite=args.overwrite,
    )
    print(
        f"wrote {len(report.cells)} cells and {len(report.comparisons)} "
        f"comparisons; passed={summary['passed']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
