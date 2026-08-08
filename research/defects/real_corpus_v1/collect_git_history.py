"""Enumerate fixing-change candidates from a pinned default-branch history."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Optional, Sequence


DEFAULT_PATTERN = r"\b(fix|bug|regression|revert)\b"


def collect_fixing_commits(
    repository_path: Path,
    *,
    repository: str,
    pinned_commit: str,
    start: str,
    end: str,
    pattern: str = DEFAULT_PATTERN,
) -> list[dict[str, Any]]:
    """Return every matching first-parent commit reachable from the pin."""

    command = [
        "git",
        "-C",
        str(repository_path),
        "log",
        pinned_commit,
        "--first-parent",
        f"--since={start}T00:00:00Z",
        f"--until={end}T23:59:59Z",
        "--format=%H%x09%cI%x09%s",
    ]
    completed = subprocess.run(
        command,
        check=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        capture_output=True,
    )
    matcher = re.compile(pattern, re.IGNORECASE)
    candidates = []
    for line in completed.stdout.splitlines():
        revision, committed_at, title = line.split("\t", 2)
        if not matcher.search(title):
            continue
        locator = f"https://github.com/{repository}/commit/{revision}"
        candidates.append({
            "repository": repository,
            "canonical_locator": locator,
            "source_kind": "fixing_commit",
            "revision": revision,
            "committed_at": committed_at,
            "title": title,
            "evidence": [{
                "evidence_id": f"{revision[:12]}-fix",
                "kind": "fixing_change",
                "locator": locator,
                "revision": revision,
            }],
        })
    return candidates


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-path", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pinned-commit", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    candidates = collect_fixing_commits(
        args.repository_path,
        repository=args.repository,
        pinned_commit=args.pinned_commit,
        start=args.start,
        end=args.end,
    )
    payload = {
        "schema_version": 1,
        "repository": args.repository,
        "pinned_commit": args.pinned_commit,
        "window": {"start": args.start, "end": args.end},
        "pattern": DEFAULT_PATTERN,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({
        "repository": args.repository,
        "pinned_commit": args.pinned_commit,
        "candidate_count": len(candidates),
        "output": str(args.output),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
