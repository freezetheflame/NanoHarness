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
    create_dispatch_record,
    preflight_batch,
    preflight_dispatch,
    validate_dispatch_record,
    validate_frozen_submission,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser("preflight")
    preflight.add_argument("--binding", type=Path, required=True)
    preflight.add_argument("--dispatch-record", type=Path)
    preflight.add_argument("--task-name", required=True)
    preflight.add_argument("--expected-annotator", required=True)
    preflight.add_argument("--expected-template", required=True)
    preflight.add_argument("--expected-output", required=True)
    preflight.add_argument("--phase", choices=("start", "end"), required=True)
    preflight.add_argument("--expected-binding-sha256")
    preflight.add_argument("--expected-freeze-payload-revision")
    preflight.add_argument("--expected-start-workspace-sha256")
    preflight.add_argument("--paper-repo", type=Path)
    preflight.add_argument("--checked-at")

    batch = commands.add_parser("preflight-batch")
    batch.add_argument("--binding", type=Path, required=True)
    batch.add_argument("--checked-at", required=True)
    batch.add_argument("--paper-repo", type=Path)

    create_record = commands.add_parser("create-dispatch-record")
    create_record.add_argument("--binding", type=Path, required=True)
    create_record.add_argument("--record", type=Path, required=True)
    create_record.add_argument("--actual-payload", type=Path, required=True)
    create_record.add_argument("--batch-preflight-json", required=True)
    create_record.add_argument(
        "--assignment", nargs=6, action="append", required=True,
        metavar=(
            "ANNOTATOR", "TASK_NAME", "TASK_ID", "STARTED_AT",
            "START_CHECKED_AT", "START_SNAPSHOT_SHA256",
        ),
    )
    create_record.add_argument("--paper-repo", type=Path)

    validate_record = commands.add_parser("validate-dispatch-record")
    validate_record.add_argument("--binding", type=Path, required=True)
    validate_record.add_argument("--record", type=Path, required=True)
    validate_record.add_argument("--paper-repo", type=Path)

    submission = commands.add_parser("validate-submission")
    submission.add_argument("--binding", type=Path, required=True)
    submission.add_argument("--dispatch-record", type=Path, required=True)
    submission.add_argument("--protocol", type=Path, required=True)
    submission.add_argument("--expected-annotator", required=True)
    submission.add_argument("--template", type=Path, required=True)
    submission.add_argument("--submission", type=Path, required=True)
    submission.add_argument("--expected-binding-sha256", required=True)
    submission.add_argument("--expected-freeze-payload-revision", required=True)
    submission.add_argument("--expected-start-workspace-sha256", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "preflight-batch":
            result = preflight_batch(
                binding_path=args.binding,
                checked_at=args.checked_at,
                paper_repo=args.paper_repo,
            )
        elif args.command == "preflight":
            result = preflight_dispatch(
                binding_path=args.binding,
                dispatch_record_path=args.dispatch_record,
                task_name=args.task_name,
                expected_annotator=args.expected_annotator,
                expected_template=args.expected_template,
                expected_output=args.expected_output,
                phase=args.phase,
                expected_binding_sha256=args.expected_binding_sha256,
                expected_freeze_payload_revision=(
                    args.expected_freeze_payload_revision
                ),
                expected_start_workspace_sha256=(
                    args.expected_start_workspace_sha256
                ),
                checked_at=args.checked_at,
                paper_repo=args.paper_repo,
            )
        elif args.command == "create-dispatch-record":
            result = create_dispatch_record(
                binding_path=args.binding,
                record_path=args.record,
                actual_payload_path=args.actual_payload,
                batch_preflight=json.loads(args.batch_preflight_json),
                assignments=[
                    {
                        "annotator_id": annotator,
                        "canonical_task_name": task_name,
                        "returned_task_id": task_id,
                        "started_at": started_at,
                        "start_preflight": {
                            "checked_at": start_checked_at,
                            "workspace_snapshot_sha256": start_snapshot,
                        },
                    }
                    for (
                        annotator, task_name, task_id, started_at,
                        start_checked_at, start_snapshot,
                    ) in args.assignment
                ],
                paper_repo=args.paper_repo,
            )
        elif args.command == "validate-dispatch-record":
            result = validate_dispatch_record(
                binding_path=args.binding,
                record_path=args.record,
                paper_repo=args.paper_repo,
            )
        else:
            result = validate_frozen_submission(
                binding_path=args.binding,
                dispatch_record_path=args.dispatch_record,
                protocol_path=args.protocol,
                expected_annotator=args.expected_annotator,
                template_path=args.template,
                submission_path=args.submission,
                expected_binding_sha256=args.expected_binding_sha256,
                expected_freeze_payload_revision=(
                    args.expected_freeze_payload_revision
                ),
                expected_start_workspace_sha256=(
                    args.expected_start_workspace_sha256
                ),
            )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({"valid": False, "error": str(error)}, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
