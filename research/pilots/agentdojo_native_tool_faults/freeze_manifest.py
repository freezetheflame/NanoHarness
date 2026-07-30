"""Freeze the AgentDojo native Tool-fault Pilot Manifest."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from nanoharness.testing import (
    FaultAction,
    FaultComponent,
    FaultExperimentManifest,
    FaultPlan,
    FaultRule,
    fault_experiment_manifest_digest,
)
from run import _benchmark, _factory, _identity


FROZEN_AT = datetime(2026, 7, 30, 14, 0, 0, tzinfo=timezone.utc)
HARNESS_REVISION = "aee9e9a430dbca7804dc4046dc3d267b6aa6f5bf"


def _plans() -> list[FaultPlan]:
    return [
        FaultPlan(
            plan_id="drop-recurring",
            rules=[
                FaultRule(
                    rule_id="drop-recurring-argument",
                    component=FaultComponent.TOOL,
                    action=FaultAction.TOOL_ARGUMENT_DROP,
                    tool_name="schedule_transaction",
                    argument="recurring",
                )
            ],
        ),
        FaultPlan(
            plan_id="stale-transaction-query",
            rules=[
                FaultRule(
                    rule_id="stale-transactions",
                    component=FaultComponent.TOOL,
                    action=FaultAction.TOOL_RESULT_STALE,
                    tool_name="get_most_recent_transactions",
                    replacement=[],
                )
            ],
        ),
        FaultPlan(
            plan_id="duplicate-schedule",
            rules=[
                FaultRule(
                    rule_id="duplicate-schedule-call",
                    component=FaultComponent.TOOL,
                    action=FaultAction.TOOL_CALL_DUPLICATE,
                    tool_name="schedule_transaction",
                )
            ],
        ),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parent
    parser.add_argument("--output", type=Path, default=root / "manifest.json")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.output.exists() and not args.overwrite:
        parser.error(f"output {args.output} exists; pass --overwrite")

    benchmark = _benchmark(root)
    identity = _identity(benchmark)
    scenario = _factory(root).scenario_for("user_task_6")
    plans = _plans()
    metadata = {
        "pilot_role": "integration_validation",
        "selection_status": "hand_selected_development_not_held_out",
        "pipeline": "AgentDojo GroundTruthPipeline",
        "scorer": "original AgentDojo user-task utility",
        "live_model": False,
        "harness_revision": HARNESS_REVISION,
        "source_benchmark_manifest_digest": benchmark.manifest_digest,
        "source_task_id": "user_task_6",
        "operator_selection": "development_examples",
    }
    experiment_id = "agentdojo-v1.2.2-banking-native-tool-fault-pilot"
    digest = fault_experiment_manifest_digest(
        experiment_id,
        identity,
        scenario,
        plans,
        frozen_at=FROZEN_AT,
        metadata=metadata,
    )
    manifest = FaultExperimentManifest(
        experiment_id=experiment_id,
        subject=identity,
        scenario=scenario,
        plans=plans,
        frozen_at=FROZEN_AT,
        manifest_digest=digest,
        metadata=metadata,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(manifest.model_dump_json(indent=2) + "\n")
    print(f"wrote {args.output}; manifest_digest={manifest.manifest_digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
