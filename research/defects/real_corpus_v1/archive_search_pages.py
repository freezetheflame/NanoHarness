"""Create a deterministic checksum-indexed ZIP of raw GitHub search pages."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any, Optional, Sequence


FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _zip_info(path: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(path, date_time=FIXED_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def archive_search_pages(raw_dir: Path, output: Path) -> dict[str, Any]:
    """Archive every repository query page with an embedded exact-byte index."""

    files = sorted(
        (
            path
            for path in raw_dir.glob("*/queries/**/*.json")
            if path.is_file()
        ),
        key=lambda path: path.relative_to(raw_dir).as_posix(),
    )
    entries = []
    payloads = []
    for path in files:
        relative = path.relative_to(raw_dir).as_posix()
        payload = path.read_bytes()
        entries.append({
            "path": relative,
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        })
        payloads.append((relative, payload))
    index = {
        "schema_version": 1,
        "file_count": len(entries),
        "files": entries,
    }
    index_bytes = (
        json.dumps(index, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        output,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        archive.writestr(_zip_info("INDEX.json"), index_bytes)
        for relative, payload in payloads:
            archive.writestr(_zip_info(relative), payload)
    return index


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    index = archive_search_pages(args.raw, args.output)
    args.index.parent.mkdir(parents=True, exist_ok=True)
    args.index.write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({
        "archive": str(args.output),
        "archive_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
        "file_count": index["file_count"],
        "index": str(args.index),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
