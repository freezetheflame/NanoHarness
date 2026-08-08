"""Build a deterministic H1 or H2 blind-coding handoff ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _zip_info(path: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(path, date_time=FIXED_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def _forbidden(path: str) -> bool:
    lowered = path.lower()
    return (
        "private" in lowered
        or "machine" in lowered
        or any(part.upper() == "H1" for part in Path(path).parts)
    )


def build_package(
    entries: Mapping[str, Path],
    output: Path,
    *,
    metadata: Mapping[str, Any],
) -> None:
    """Write exact named inputs plus metadata and a checksum inventory."""

    forbidden = sorted(path for path in entries if _forbidden(path))
    if forbidden:
        raise ValueError(f"forbidden blind-coding material: {forbidden}")
    payloads = {
        path.replace("\\", "/"): source.read_bytes()
        for path, source in entries.items()
    }
    metadata_bytes = (
        json.dumps(metadata, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    payloads["PACKAGE_METADATA.json"] = metadata_bytes
    checksum_lines = [
        f"{hashlib.sha256(payloads[path]).hexdigest()}  {path}"
        for path in sorted(payloads)
    ]
    checksum_bytes = ("\n".join(checksum_lines) + "\n").encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        output,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in sorted(payloads):
            archive.writestr(_zip_info(path), payloads[path])
        archive.writestr(_zip_info("PACKAGE_SHA256SUMS"), checksum_bytes)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper-root", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--coder", choices=("H2",), default="H2")
    parser.add_argument("--paper-commit", required=True)
    parser.add_argument("--artifact-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    paper_package = args.paper_root / "experiments" / "defects" / "real-corpus-v1"
    artifact_package = args.artifact_root / "research" / "defects" / "real_corpus_v1"
    entries = {
        "docs/DEFECT_CODING_MANUAL.md": (
            args.paper_root / "experiments" / "design" / "DEFECT_CODING_MANUAL.md"
        ),
        "docs/HUMAN_CODER_INSTRUCTIONS.md": paper_package / "HUMAN_CODER_INSTRUCTIONS.md",
        "docs/QUESTION_LOG.md": paper_package / "QUESTION_LOG.md",
        "docs/RULE_ERRATA.md": paper_package / "RULE_ERRATA.md",
        "evidence/evidence_packet.json": artifact_package / "evidence_packet.json",
        "coding/pass_a.json": artifact_package / "coding_templates" / args.coder / "pass_a.json",
    }
    for patch in sorted((artifact_package / "patches").glob("*.patch")):
        entries[f"evidence/patches/{patch.name}"] = patch
    packet_bytes = entries["evidence/evidence_packet.json"].read_bytes()
    metadata = {
        "schema_version": 1,
        "coder_id": args.coder,
        "manual_version": "1.0",
        "packet_sha256": hashlib.sha256(packet_bytes).hexdigest(),
        "candidate_count": 77,
        "paper_commit": args.paper_commit,
        "artifact_commit": args.artifact_commit,
        "forbidden_material_excluded": [
            "hidden partitions",
            "machine pre-code",
            "H1 coding file",
            "Operator Catalog",
        ],
    }
    build_package(entries, args.output, metadata=metadata)
    print(json.dumps({
        "output": str(args.output),
        "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
        "coder_id": args.coder,
        "candidate_count": metadata["candidate_count"],
        "entry_count": len(entries) + 2,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
