"""Create a conservative non-human pre-code hidden from independent coders."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


DOCUMENT_EXTENSIONS = {".md", ".mdx", ".rst", ".txt"}
DEPENDENCY_FILES = {
    "package-lock.json",
    "poetry.lock",
    "uv.lock",
    "yarn.lock",
    "pnpm-lock.yaml",
}
BOUNDARY_TERMS = {
    "model": ("model", "llm", "response parser"),
    "tool": ("tool", "function call", "retry", "duplicate"),
    "context": ("context", "prompt", "message", "memory", "compact"),
    "state": ("state", "checkpoint", "persist", "cache", "namespace"),
    "hook": ("hook", "callback", "lifecycle"),
    "evaluator": ("evaluate", "evaluator", "reward", "score", "grader"),
    "permission": ("permission", "policy", "authoriz", "approval"),
    "control_flow": (
        "cancel",
        "termination",
        "delegate",
        "workflow",
        "graph",
        "loop",
        "schedule",
    ),
    "replay": ("replay", "recording"),
}


def _documentation_only(paths: Sequence[str]) -> bool:
    return bool(paths) and all(
        Path(path).suffix.lower() in DOCUMENT_EXTENSIONS
        or path.lower().startswith(("docs/", "documentation/"))
        for path in paths
    )


def machine_precode(
    candidates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return auditable heuristic suggestions that are never human evidence."""

    output = []
    for candidate in candidates:
        paths = [str(path) for path in candidate.get("changed_files", [])]
        text = " ".join([
            str(candidate.get("title", "")),
            str(candidate.get("commit_message", "")),
            " ".join(paths),
        ]).lower()
        boundaries = [
            boundary
            for boundary, terms in BOUNDARY_TERMS.items()
            if any(term in text for term in terms)
        ]
        exclusion_reason = ""
        if _documentation_only(paths):
            decision = "exclude"
            exclusion_reason = "doc_or_format_only"
        elif paths and all(Path(path).name.lower() in DEPENDENCY_FILES for path in paths):
            decision = "exclude"
            exclusion_reason = "dependency_only"
        elif boundaries and candidate.get("candidate_test_files"):
            decision = "include"
        else:
            decision = "uncertain"
        output.append({
            "defect_id": candidate["defect_id"],
            "provisional_decision": decision,
            "provisional_exclusion_reason": exclusion_reason,
            "provisional_boundaries": boundaries,
            "evidence_ids": [
                item["evidence_id"]
                for item in candidate.get("evidence", [])
                if "evidence_id" in item
            ],
            "rationale": (
                "Heuristic machine pre-code based only on commit terms, changed "
                "paths, and candidate test paths; requires human evidence review."
            ),
        })
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selected", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    selected = json.loads(args.selected.read_text(encoding="utf-8"))
    entries = machine_precode(selected["candidates"])
    payload = {
        "schema_version": 1,
        "corpus_id": "agent-real-defects-v1",
        "role": "non-human-private-precode",
        "claim_warning": (
            "These heuristic suggestions are hidden from H1/H2 and excluded "
            "from all agreement and defect-count calculations."
        ),
        "candidate_count": len(entries),
        "entries": entries,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({
        "candidate_count": len(entries),
        "output": str(args.output),
        "role": payload["role"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
