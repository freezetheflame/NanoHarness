"""Preflight frozen agent dispatch and validate one bound Pass A submission."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from research.defects.real_corpus_v1.agent_dispatch import (
    preflight_dispatch,
    validate_frozen_submission,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser("preflight")
    preflight.add_argument("--binding", type=Path, required=True)
    preflight.add_argument("--task-name", required=True)
    preflight.add_argument("--expected-annotator", required=True)
    preflight.add_argument("--expected-template", required=True)
    preflight.add_argument("--expected-output", required=True)
    preflight.add_argument("--phase", choices=("start", "end"), required=True)
    preflight.add_argument("--expected-binding-sha256")
    preflight.add_argument("--expected-freeze-payload-revision")
    preflight.add_argument("--paper-repo", type=Path)

    submission = commands.add_parser("validate-submission")
    submission.add_argument("--binding", type=Path, required=True)
    submission.add_argument("--protocol", type=Path, required=True)
    submission.add_argument("--expected-annotator", required=True)
    submission.add_argument("--template", type=Path, required=True)
    submission.add_argument("--submission", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "preflight":
            result = preflight_dispatch(
                binding_path=args.binding,
                task_name=args.task_name,
                expected_annotator=args.expected_annotator,
                expected_template=args.expected_template,
                expected_output=args.expected_output,
                phase=args.phase,
                expected_binding_sha256=args.expected_binding_sha256,
                expected_freeze_payload_revision=(
                    args.expected_freeze_payload_revision
                ),
                paper_repo=args.paper_repo,
            )
        else:
            result = validate_frozen_submission(
                binding_path=args.binding,
                protocol_path=args.protocol,
                expected_annotator=args.expected_annotator,
                template_path=args.template,
                submission_path=args.submission,
            )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({"valid": False, "error": str(error)}, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
