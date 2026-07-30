"""Run the frozen LangGraph tool-fault Pilot and archive Campaign JSON."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from nanoharness.components.tools.dict_registry import DictToolRegistry
from nanoharness.core.schema import (
    EvaluationResult,
    RunResult,
    RunStatus,
    StopReason,
)
from nanoharness.testing import (
    FaultExperimentManifest,
    FaultInjectingToolRegistry,
    FaultSession,
    LangGraphSubjectAdapter,
    RecordingToolRegistry,
    Scenario,
    SubjectFaultCampaignRunner,
)


class ToolState(TypedDict, total=False):
    query: str
    tool_name: str
    arguments: dict
    observation: Any
    answer: str


def build_adapter(session: FaultSession | None):
    def graph_builder(scenario, recorder):
        registry = DictToolRegistry()
        call_count = 0

        @registry.tool
        def echo(text: str):
            """Return deterministic content plus a side-effect counter."""
            nonlocal call_count
            call_count += 1
            return {"echo": text, "call_number": call_count}

        if session is None:
            tools = RecordingToolRegistry(registry, recorder)
        else:
            tools = FaultInjectingToolRegistry(
                registry,
                session,
                recorder=recorder,
            )

        graph = StateGraph(ToolState)
        graph.add_node(
            "plan",
            lambda state: {
                "tool_name": "echo",
                "arguments": {"text": state["query"].strip().casefold()},
            },
        )
        graph.add_node(
            "tool",
            lambda state: {
                "observation": tools.call(
                    state["tool_name"],
                    state["arguments"],
                )
            },
        )
        graph.add_node(
            "answer",
            lambda state: {"answer": state["observation"]["echo"]},
        )
        graph.add_edge(START, "plan")
        graph.add_edge("plan", "tool")
        graph.add_edge("tool", "answer")
        graph.add_edge("answer", END)
        return graph.compile()

    return LangGraphSubjectAdapter.from_installed_builder(
        graph_builder,
        lambda scenario: {"query": scenario.query},
        map_result,
    )


def map_result(scenario: Scenario, final_state, snapshots):
    expected = scenario.fixtures["expected_answer"]
    achieved = final_state.get("answer") == expected
    return RunResult(
        status=RunStatus.COMPLETED,
        stop_reason=StopReason.SUBJECT_COMPLETED,
        final_answer=final_state.get("answer"),
        evaluation=EvaluationResult(
            achieved=achieved,
            confidence=1.0,
            explanation="Exact deterministic answer comparison",
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
        default=root / "raw" / "fault_campaign.json",
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.output.exists() and not args.overwrite:
        parser.error(
            f"output {args.output} already exists; choose another path or "
            "pass --overwrite"
        )
    manifest = FaultExperimentManifest.model_validate_json(
        args.manifest.read_text()
    )
    report = SubjectFaultCampaignRunner(build_adapter).run(manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report.model_dump_json(indent=2) + "\n")
    print(
        f"wrote {len(report.campaign.outcomes)} fault outcomes to {args.output}; "
        f"killed={report.campaign.killed}, "
        f"score={report.campaign.mutation_score}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
