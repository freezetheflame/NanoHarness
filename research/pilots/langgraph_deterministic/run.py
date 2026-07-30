"""Run the frozen deterministic LangGraph pilot and write raw JSON results."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from nanoharness.core.schema import (
    EvaluationResult,
    RunResult,
    RunStatus,
    StopReason,
)
from nanoharness.testing import (
    ExperimentManifest,
    ExperimentRunner,
    LangGraphSubjectAdapter,
    Scenario,
)


class EchoState(TypedDict, total=False):
    query: str
    normalized: str
    answer: str


def build_graph():
    graph = StateGraph(EchoState)
    graph.add_node(
        "normalize",
        lambda state: {"normalized": state["query"].strip().casefold()},
    )
    graph.add_node(
        "answer",
        lambda state: {"answer": f"echo:{state['normalized']}"},
    )
    graph.add_edge(START, "normalize")
    graph.add_edge("normalize", "answer")
    graph.add_edge("answer", END)
    return graph.compile()


def build_input(scenario: Scenario):
    return {"query": scenario.query}


def map_result(scenario, final_state, snapshots):
    expected = scenario.fixtures["expected"]
    achieved = final_state.get("answer") == expected
    return RunResult(
        status=RunStatus.COMPLETED,
        stop_reason=StopReason.SUBJECT_COMPLETED,
        final_answer=final_state.get("answer"),
        evaluation=EvaluationResult(
            achieved=achieved,
            confidence=1.0,
            explanation="Exact deterministic final-state comparison",
            evidence=[
                f"expected={expected!r}",
                f"actual={final_state.get('answer')!r}",
                f"snapshots={len(snapshots)}",
            ],
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parent
    parser.add_argument("--manifest", type=Path, default=root / "manifest.json")
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "raw" / "pilot_report.json",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Explicitly replace an existing output artifact",
    )
    args = parser.parse_args()
    if args.output.exists() and not args.overwrite:
        parser.error(
            f"output {args.output} already exists; choose another path or "
            "pass --overwrite"
        )
    manifest = ExperimentManifest.model_validate_json(args.manifest.read_text())
    adapter = LangGraphSubjectAdapter.from_installed(
        build_graph,
        build_input,
        map_result,
    )
    report = ExperimentRunner([adapter]).run(manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report.model_dump_json(indent=2) + "\n")
    print(
        f"wrote {len(report.observations)} observations to {args.output}; "
        f"pass_rate={report.subjects[0].pass_rate}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
