from datetime import datetime, timezone

import pytest

pytest.importorskip("tau2")

from tau2.data_model.message import (  # noqa: E402
    AssistantMessage,
    ToolCall,
    UserMessage,
)
from tau2.data_model.simulation import SimulationRun, TerminationReason  # noqa: E402
from tau2.domains.retail.environment import get_environment  # noqa: E402
from tau2.domains.retail.utils import (  # noqa: E402
    RETAIL_DB_PATH,
    RETAIL_POLICY_PATH,
    RETAIL_TASK_SET_PATH,
)

from nanoharness.testing import (  # noqa: E402
    TAU2_REVISION,
    SubjectIdentity,
    Tau2BenchmarkAdapter,
    Tau2SubjectAdapter,
)


def _reference_simulation(scenario, task, recorder):
    initial = task.initial_state
    messages = list(initial.message_history or []) if initial else []
    environment = get_environment(solo_mode=False)
    environment.set_state(
        initialization_data=(initial.initialization_data if initial else None),
        initialization_actions=(initial.initialization_actions if initial else None),
        message_history=list(messages),
        strict=True,
    )
    for action in task.evaluation_criteria.actions or []:
        call = ToolCall(
            id=action.action_id,
            name=action.name,
            arguments=action.arguments,
            requestor=action.requestor,
        )
        message_class = UserMessage if action.requestor == "user" else AssistantMessage
        messages.append(
            message_class(role=action.requestor, content=None, tool_calls=[call])
        )
        messages.append(environment.get_response(call))
    messages.append(AssistantMessage(role="assistant", content="Reference complete."))
    return SimulationRun(
        id=f"nanoharness-reference-{task.id}",
        task_id=task.id,
        start_time="2026-07-31T00:00:00Z",
        end_time="2026-07-31T00:00:01Z",
        duration=1.0,
        termination_reason=TerminationReason.AGENT_STOP,
        messages=messages,
        seed=scenario.seed,
    )


def test_pinned_tau2_native_db_state_and_side_effect_bridge():
    manifest = Tau2BenchmarkAdapter.from_files(
        RETAIL_TASK_SET_PATH,
        domain_db_path=RETAIL_DB_PATH,
        policy_path=RETAIL_POLICY_PATH,
    ).convert_tasks(
        "retail",
        ["7", "62"],
        frozen_at=datetime(2026, 7, 31, tzinfo=timezone.utc),
        selection_provenance={"purpose": "native-bridge-integration"},
    )
    identity = SubjectIdentity(
        subject_id="tau2-reference-trajectory@v1.0.1",
        runtime="tau2",
        version="1.0.1",
        revision=TAU2_REVISION,
        source_url="https://github.com/sierra-research/tau2-bench",
        independently_developed=True,
        metadata={"execution": "reference-trajectory-plumbing"},
    )
    adapter = Tau2SubjectAdapter.from_installed(
        identity,
        manifest,
        _reference_simulation,
    )

    reports = [
        adapter.run(adapter.scenario_for(task_id, seed=0))
        for task_id in ("7", "62")
    ]

    assert all(report.passed for report in reports)
    assert all(report.result.evaluation.achieved for report in reports)
    assert all(len(report.verdicts) == 3 for report in reports)
    assert all(all(verdict.passed for verdict in report.verdicts) for report in reports)
    assert reports[0].verdicts[1].evidence["changes"]
    assert reports[0].verdicts[2].evidence["attempt_counts"]
    assert reports[1].verdicts[1].evidence["changes"] == {}
    assert reports[1].verdicts[2].evidence["attempt_counts"] == {}
