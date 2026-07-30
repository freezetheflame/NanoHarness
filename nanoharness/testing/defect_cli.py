"""Command-line summaries and pre-freeze gates for a defect corpus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional, Sequence

from nanoharness.testing.defects import DefectCorpus, DefectCorpusAnalyzer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path, help="Path to a DefectCorpus JSON file")
    parser.add_argument(
        "--annotators",
        nargs=2,
        metavar=("CODER_A", "CODER_B"),
        help="Two coder IDs used for agreement analysis",
    )
    parser.add_argument(
        "--check-readiness",
        action="store_true",
        help="Evaluate preregistered freeze gates and fail if they do not pass",
    )
    parser.add_argument("--minimum-verified", type=int, default=0)
    parser.add_argument("--minimum-validation", type=int, default=0)
    parser.add_argument("--minimum-double-coded", type=int, default=0)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    corpus = DefectCorpus.model_validate_json(args.corpus.read_text())
    summary = DefectCorpusAnalyzer.summarize(
        corpus,
        annotator_pair=args.annotators,
    )
    output = {"summary": summary.model_dump(mode="json")}
    ready = True
    if args.check_readiness:
        readiness = DefectCorpusAnalyzer.assess_readiness(
            corpus,
            minimum_verified=args.minimum_verified,
            minimum_validation=args.minimum_validation,
            minimum_double_coded=args.minimum_double_coded,
            annotator_pair=args.annotators,
        )
        output["readiness"] = readiness.model_dump(mode="json")
        ready = readiness.ready
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
