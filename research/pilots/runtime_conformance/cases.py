"""Deterministic M1--M4 fixtures for NanoHarness and LangGraph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from nanoharness.components.context.simple_context import SimpleContextManager
from nanoharness.components.evaluator.trace_evaluator import TraceEvaluator
from nanoharness.components.hooks.simple_hooks import SimpleHookManager
from nanoharness.components.tools.dict_registry import DictToolRegistry
from nanoharness.core.base import BaseStateStore
from nanoharness.core.engine import NanoEngine
from nanoharness.core.schema import (
    EvaluationResult,
    LLMResponse,
    RunResult,
    RunStatus,
    StopReason,
    ToolCall,
)
from nanoharness.testing import (
    CaseConformanceResult,
    LangGraphSubjectAdapter,
    OracleKind,
    OracleSpec,
    PermissionDecisionProjection,
    ProjectionExpectation,
    RecordingLLM,
    RecordingPermissionManager,
    RecordingToolRegistry,
    RuntimeCaseEvidence,
    Scenario,
    ScenarioRunner,
    ScenarioRunnerAdapter,
    SemanticProjection,
    SubjectIdentity,
    ToolAttemptProjection,
    TraceEventType,
    compare_projections,
    project_scenario_report,
)


CASE_IDS = ("M1", "M2", "M3", "M4")
STOP_REASON_EQUIVALENCES = {
    "model_terminated": "normal_termination",
    "subject_completed": "normal_termination",
}
LOOKUP_RESULT = {"status": "open"}
DENIAL = "write denied by policy"


class TransientToolError(RuntimeError):
    """Deterministic M2 boundary failure."""


class StaticLLM:
    def __init__(self, responses: List[LLMResponse]):
        self._responses = list(responses)
        self._index = 0

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        response = self._responses[self._index]
        self._index += 1
        return response


class MemoryStateStore(BaseStateStore):
    def __init__(self):
        self.value: Dict[str, Any] = {}

    def save_state(self, state: Dict[str, Any]):
        self.value = dict(state)

    def load_state(self) -> Dict[str, Any]:
        return dict(self.value)

    def reset(self):
        self.value = {}


class FixedEvaluator(TraceEvaluator):
    def __init__(self, achieved: bool):
        super().__init__()
        self._achieved = achieved

    def evaluate_success(self, query, trajectory):
        return EvaluationResult(
            achieved=self._achieved,
            confidence=1.0,
            explanation="Frozen M1--M4 semantic expectation",
        )


class DenyWrites:
    def enforce(self, tool_name: str, args: Dict) -> Optional[str]:
        return DENIAL if tool_name == "update_order" else None


class ExecutableTools:
    """Fresh LangGraph fixture tools with independently auditable attempts."""

    def __init__(self, *, fail_first_lookup: bool = False):
        self.lookup_attempts = 0
        self.update_attempts = 0
        self._fail_first_lookup = fail_first_lookup

    def lookup_order(self, order_id: int):
        self.lookup_attempts += 1
        if self._fail_first_lookup and self.lookup_attempts == 1:
            raise TransientToolError("retryable")
        return LOOKUP_RESULT

    def update_order(self, order_id: int, status: str):
        self.update_attempts += 1
        return {"order_id": order_id, "status": status}


class ConformanceState(TypedDict, total=False):
    query: str
    answer: str
    achieved: bool


@dataclass(frozen=True)
class CasePairExecution:
    case_id: str
    expected: ProjectionExpectation
    nanoharness: RuntimeCaseEvidence
    langgraph: RuntimeCaseEvidence
    comparison: CaseConformanceResult


def scenario_for(case_id: str) -> Scenario:
    if case_id not in CASE_IDS:
        raise ValueError(f"Unknown conformance case {case_id!r}")
    achieved = case_id in {"M1", "M2"}
    oracles = [
        OracleSpec(
            oracle_id=f"{case_id.lower()}-goal",
            kind=OracleKind.GOAL_ACHIEVEMENT,
            parameters={"expected": case_id != "M4"},
        ),
        OracleSpec(
            oracle_id=f"{case_id.lower()}-status",
            kind=OracleKind.RUN_STATUS,
            parameters={"allowed": ["completed"]},
        ),
        OracleSpec(
            oracle_id=f"{case_id.lower()}-lifecycle",
            kind=OracleKind.LIFECYCLE,
        ),
    ]
    if case_id == "M1":
        oracles.extend(_lookup_oracles(case_id, count=1))
    elif case_id == "M2":
        oracles.extend(_lookup_oracles(case_id, count=2))
        oracles.append(
            OracleSpec(
                oracle_id="m2-component-errors",
                kind=OracleKind.COMPONENT_ERRORS,
                parameters={"allowed_components": ["tool"]},
            )
        )
    elif case_id == "M4":
        oracles.append(
            OracleSpec(
                oracle_id="m4-permission",
                kind=OracleKind.PERMISSION_ENFORCEMENT,
                parameters={
                    "require_decision_for_execution": True,
                    "denied_must_not_execute": True,
                },
            )
        )
    return Scenario(
        scenario_id=case_id,
        query={
            "M1": "Look up order 7 once.",
            "M2": "Look up order 7 and recover from one transient error.",
            "M3": "Stop without completing the requested lookup.",
            "M4": "Update order 7 to closed even though writes are denied.",
        }[case_id],
        seed=20260808,
        tags=["runtime-conformance", case_id.lower(), "deterministic"],
        fixtures={"case_id": case_id, "achieved": achieved},
        oracles=oracles,
    )


def expectation_for(case_id: str) -> ProjectionExpectation:
    lookup = ToolAttemptProjection(
        name="lookup_order",
        arguments={"order_id": 7},
        outcome="success",
        result=LOOKUP_RESULT,
        observation_delivered=True,
    )
    attempts = []
    permissions = []
    recovery = False
    achieved = case_id in {"M1", "M2"}
    report_passed = case_id != "M3"
    if case_id == "M1":
        attempts = [lookup]
    elif case_id == "M2":
        attempts = [
            ToolAttemptProjection(
                name="lookup_order",
                arguments={"order_id": 7},
                outcome="error",
                error_type="TransientToolError",
                observation_delivered=False,
            ),
            lookup,
        ]
        recovery = True
    elif case_id == "M4":
        permissions = [
            PermissionDecisionProjection(
                tool_name="update_order",
                arguments={"order_id": 7, "status": "closed"},
                allowed=False,
                denial=DENIAL,
            )
        ]
    return ProjectionExpectation(
        case_id=case_id,
        run_status="completed",
        stop_reason="normal_termination",
        goal_achieved=achieved,
        report_passed=report_passed,
        execution_error_type=None,
        tool_attempts=attempts,
        permission_decisions=permissions,
        recovery_observed=recovery,
        lifecycle_start_count=1,
        lifecycle_end_count=1,
        lifecycle_paired=True,
        event_ids_unique=True,
        sequences_monotonic=True,
        trace_ids_consistent=True,
        scenario_round_trip=True,
        report_round_trip=True,
    )


def run_case_pair(case_id: str) -> CasePairExecution:
    scenario = scenario_for(case_id)
    nano_report = _nanoharness_adapter().run(scenario)
    graph_report = _langgraph_adapter().run(scenario)
    nano_projection = project_scenario_report(
        case_id, "nanoharness", scenario, nano_report
    )
    graph_projection = project_scenario_report(
        case_id, "langgraph", scenario, graph_report
    )
    expected = expectation_for(case_id)
    nano_evidence = RuntimeCaseEvidence(
        case_id=case_id,
        runtime="nanoharness",
        scenario=scenario,
        report=nano_report,
        projection=nano_projection,
    )
    graph_evidence = RuntimeCaseEvidence(
        case_id=case_id,
        runtime="langgraph",
        scenario=scenario,
        report=graph_report,
        projection=graph_projection,
    )
    comparison = compare_projections(
        expected,
        nano_projection,
        graph_projection,
        stop_reason_equivalences=STOP_REASON_EQUIVALENCES,
    )
    return CasePairExecution(
        case_id=case_id,
        expected=expected,
        nanoharness=nano_evidence,
        langgraph=graph_evidence,
        comparison=comparison,
    )


def _lookup_oracles(case_id: str, count: int) -> list[OracleSpec]:
    return [
        OracleSpec(
            oracle_id=f"{case_id.lower()}-tool-call",
            kind=OracleKind.TOOL_CALLS,
            parameters={
                "required": ["lookup_order"],
                "min_counts": {"lookup_order": count},
                "max_counts": {"lookup_order": count},
                "expected_arguments": {"lookup_order": {"order_id": 7}},
            },
        ),
        OracleSpec(
            oracle_id=f"{case_id.lower()}-tool-result",
            kind=OracleKind.TOOL_RESULTS,
            parameters={"expected_last": {"lookup_order": LOOKUP_RESULT}},
        ),
    ]


def _nanoharness_adapter() -> ScenarioRunnerAdapter:
    identity = SubjectIdentity(
        subject_id="nanoharness@runtime-conformance",
        runtime="nanoharness",
        version="0.1.0",
        revision="runtime-conformance",
        independently_developed=False,
    )
    return ScenarioRunnerAdapter(identity, ScenarioRunner(_build_nano_engine))


def _build_nano_engine(scenario: Scenario, recorder) -> NanoEngine:
    case_id = scenario.fixtures["case_id"]
    registry = DictToolRegistry()
    attempts = {"lookup": 0, "update": 0}

    @registry.tool
    def lookup_order(order_id: int):
        """Look up one order."""
        attempts["lookup"] += 1
        if case_id == "M2" and attempts["lookup"] == 1:
            raise TransientToolError("retryable")
        return LOOKUP_RESULT

    @registry.tool
    def update_order(order_id: int, status: str):
        """Update one order."""
        attempts["update"] += 1
        return {"order_id": order_id, "status": status}

    call_lookup = LLMResponse(
        content="lookup",
        tool_calls=[ToolCall(name="lookup_order", arguments={"order_id": 7})],
    )
    responses = {
        "M1": [call_lookup, LLMResponse(content="order is open")],
        "M2": [call_lookup, call_lookup, LLMResponse(content="order is open")],
        "M3": [LLMResponse(content="stopping without lookup")],
        "M4": [
            LLMResponse(
                content="update",
                tool_calls=[
                    ToolCall(
                        name="update_order",
                        arguments={"order_id": 7, "status": "closed"},
                    )
                ],
            ),
            LLMResponse(content="write was denied"),
        ],
    }[case_id]
    permissions = (
        RecordingPermissionManager(DenyWrites(), recorder)
        if case_id == "M4"
        else None
    )
    return NanoEngine(
        llm_client=RecordingLLM(StaticLLM(responses), recorder),
        tools=RecordingToolRegistry(registry, recorder),
        context=SimpleContextManager(),
        state=MemoryStateStore(),
        hooks=SimpleHookManager(),
        evaluator=FixedEvaluator(bool(scenario.fixtures["achieved"])),
        permissions=permissions,
    )


def _langgraph_adapter() -> LangGraphSubjectAdapter:
    return LangGraphSubjectAdapter.from_installed_builder(
        _build_langgraph,
        lambda scenario: {"query": scenario.query},
        _map_langgraph_result,
    )


def _build_langgraph(scenario: Scenario, recorder):
    case_id = scenario.fixtures["case_id"]
    tools = ExecutableTools(fail_first_lookup=case_id == "M2")

    def execute(state):
        if case_id == "M1":
            result = tools.lookup_order(7)
            _record_lookup_success(recorder, result)
        elif case_id == "M2":
            try:
                tools.lookup_order(7)
            except TransientToolError as exc:
                recorder.record(
                    TraceEventType.TOOL_ERROR,
                    {
                        "name": "lookup_order",
                        "arguments": {"order_id": 7},
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                        "observation_delivered": False,
                    },
                )
            result = tools.lookup_order(7)
            _record_lookup_success(recorder, result)
        elif case_id == "M4":
            arguments = {"order_id": 7, "status": "closed"}
            denial = DenyWrites().enforce("update_order", arguments)
            recorder.record(
                TraceEventType.PERMISSION_DECISION,
                {
                    "tool_name": "update_order",
                    "arguments": arguments,
                    "allowed": denial is None,
                    "denial": denial,
                },
            )
            if denial is None:
                result = tools.update_order(**arguments)
                recorder.record(
                    TraceEventType.TOOL_EXCHANGE,
                    {
                        "name": "update_order",
                        "arguments": arguments,
                        "result": result,
                        "observation_delivered": True,
                    },
                )
        recorder.record(
            TraceEventType.CUSTOM,
            {
                "adapter": "langgraph",
                "adapter_event": "boundary_audit",
                "lookup_attempts": tools.lookup_attempts,
                "update_attempts": tools.update_attempts,
            },
        )
        return {
            "answer": "order is open" if case_id in {"M1", "M2"} else "stopped",
            "achieved": bool(scenario.fixtures["achieved"]),
        }

    graph = StateGraph(ConformanceState)
    graph.add_node("execute", execute)
    graph.add_edge(START, "execute")
    graph.add_edge("execute", END)
    return graph.compile()


def _record_lookup_success(recorder, result) -> None:
    recorder.record(
        TraceEventType.TOOL_EXCHANGE,
        {
            "name": "lookup_order",
            "arguments": {"order_id": 7},
            "result": result,
            "observation_delivered": True,
        },
    )


def _map_langgraph_result(scenario, final_state, snapshots) -> RunResult:
    return RunResult(
        status=RunStatus.COMPLETED,
        stop_reason=StopReason.SUBJECT_COMPLETED,
        final_answer=final_state.get("answer"),
        evaluation=EvaluationResult(
            achieved=bool(final_state.get("achieved")),
            confidence=1.0,
            explanation="Frozen M1--M4 semantic expectation",
        ),
    )
