"""Archive immutable commit evidence for the mechanically selected candidates."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


PR_SUFFIX = re.compile(r"\(#(?P<number>\d+)\)\s*$")
TEST_PATH = re.compile(
    r"(^|/)(tests?/|test_|[^/]+_test\.|[^/]+\.spec\.)",
    re.IGNORECASE,
)


def _git_bytes(repository_path: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repository_path), *arguments],
        check=True,
        capture_output=True,
    ).stdout


def _git_text(repository_path: Path, *arguments: str) -> str:
    return _git_bytes(repository_path, *arguments).decode("utf-8", errors="strict")


def enrich_candidates(
    selected: Sequence[Mapping[str, Any]],
    *,
    repository_paths: Mapping[str, Path],
    output_dir: Path,
) -> list[dict[str, Any]]:
    """Add commit messages, changed paths, PR locators, and exact patch hashes."""

    patches_dir = output_dir / "patches"
    patches_dir.mkdir(parents=True, exist_ok=True)
    enriched = []
    for candidate in selected:
        repository = str(candidate["repository"])
        if repository not in repository_paths:
            raise ValueError(f"missing repository path for {repository}")
        repository_path = repository_paths[repository]
        revision = str(candidate["revision"])
        message = _git_text(repository_path, "show", "-s", "--format=%B", revision).strip()
        changed_files = sorted(set(filter(None, _git_text(
            repository_path,
            "diff-tree",
            "--root",
            "--no-commit-id",
            "--name-only",
            "-r",
            "-m",
            revision,
        ).splitlines())))
        patch_bytes = _git_bytes(
            repository_path,
            "show",
            "--format=email",
            "--binary",
            "--find-renames",
            revision,
        )
        patch_relative = Path("patches") / f"{candidate['defect_id']}.patch"
        patch_path = output_dir / patch_relative
        patch_path.write_bytes(patch_bytes)

        item = copy.deepcopy(dict(candidate))
        item["commit_message"] = message
        item["changed_files"] = changed_files
        item["candidate_test_files"] = [
            path for path in changed_files if TEST_PATH.search(path)
        ]
        item["patch_path"] = patch_relative.as_posix()
        item["patch_sha256"] = hashlib.sha256(patch_bytes).hexdigest()
        match = PR_SUFFIX.search(str(candidate["title"]))
        item["pull_request_locator"] = (
            f"https://github.com/{repository}/pull/{match.group('number')}"
            if match
            else None
        )
        enriched.append(item)
    return enriched


def _repository_mapping(values: Sequence[str]) -> dict[str, Path]:
    mapping = {}
    for value in values:
        repository, separator, path = value.partition("=")
        if not separator or not repository or not path:
            raise ValueError(
                "--repository-path must use owner/name=absolute-or-relative-path"
            )
        mapping[repository] = Path(path)
    return mapping


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selected", type=Path, required=True)
    parser.add_argument("--repository-path", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    selected_payload = json.loads(args.selected.read_text(encoding="utf-8"))
    enriched = enrich_candidates(
        selected_payload["candidates"],
        repository_paths=_repository_mapping(args.repository_path),
        output_dir=args.output,
    )
    output = args.output / "selected_candidates.enriched.private.json"
    output.write_text(
        json.dumps({
            "schema_version": 1,
            "manifest_id": selected_payload["manifest_id"],
            "candidate_count": len(enriched),
            "candidates": enriched,
        }, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({
        "candidate_count": len(enriched),
        "output": str(output),
        "patch_directory": str(args.output / "patches"),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
