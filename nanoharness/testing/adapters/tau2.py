"""Native grader and state-evidence bridge for pinned tau2 tasks."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from importlib import import_module, metadata
from pathlib import Path
from typing import Any, Optional, Protocol

from nanoharness.core.schema import (
    EvaluationResult,
    RunResult,
    RunStatus,
    StopReason,
)
from nanoharness.testing.adapters.base import CallableSubjectAdapter, SubjectIdentity
from nanoharness.testing.benchmarks.base import (
    BenchmarkConfigurationError,
    BenchmarkManifest,
    BenchmarkSourceIdentity,
    canonical_value_digest,
)
from nanoharness.testing.benchmarks.tau2 import TAU2_DISTRIBUTION
from nanoharness.testing.scenario import OracleKind, OracleSpec, Scenario, ScenarioReport
from nanoharness.testing.trace import TraceEventType, TraceRecorder, normalize_trace_value


TAU2_NATIVE_BINDING = "tau2_native_t0_db_t2"


class Tau2UnavailableError(ImportError):
    """Raised when the pinned tau2 runtime is unavailable."""


class Tau2SimulationFactory(Protocol):
    """Produce one native half-duplex tau2 SimulationRun for a bound task."""

    def __call__(self, scenario: Scenario, task: Any, recorder: TraceRecorder) -> Any:
        ...


class Tau2SubjectAdapter:
    """Execute and grade converted tau2 tasks with native deterministic semantics.

    The bridge keeps the source Benchmark Manifest scorer-unbound. ``scenario_for``
    derives a new execution Scenario containing the native DB grader plus process
    Oracles over a task-derived state delta and a semantic side-effect ledger.
    Reference actions are replayed only to derive the target state, never compared
    directly with the subject's Tool-call sequence.
    """

    def __init__(
        self,
        identity: SubjectIdentity,
        benchmark_manifest: BenchmarkManifest,
        simulation_factory: Tau2SimulationFactory,
        task_loader: Callable[[], Sequence[Any]],
        environment_constructor: Callable[..., Any],
        environment_evaluator: Any,
        *,
        installed_source: Optional[BenchmarkSourceIdentity] = None,
    ):
        if identity.runtime.casefold() != "tau2":
            raise ValueError("tau2 subject identity runtime must be 'tau2'")
        benchmark_manifest.assert_unchanged()
        if benchmark_manifest.source.distribution.casefold() != TAU2_DISTRIBUTION:
            raise BenchmarkConfigurationError("tau2 bridge requires a tau2 Manifest")
        if benchmark_manifest.oracle_binding != "unbound":
            raise BenchmarkConfigurationError(
                "tau2 source Manifest must remain scorer-unbound"
            )
        if installed_source is not None and benchmark_manifest.source != installed_source:
            raise BenchmarkConfigurationError(
                "Installed tau2 source does not match Benchmark Manifest"
            )
        self._identity = SubjectIdentity.model_validate(identity.model_dump())
        self._manifest = BenchmarkManifest.model_validate(
            benchmark_manifest.model_dump()
        )
        self._simulation_factory = simulation_factory
        self._task_loader = task_loader
        self._environment_constructor = environment_constructor
        self._environment_evaluator = environment_evaluator
        self._records = {
            record.source_task_id: record for record in self._manifest.tasks
        }
        native_tasks = list(self._task_loader())
        self._native_tasks = {str(task.id): task for task in native_tasks}
        missing = sorted(set(self._records) - set(self._native_tasks))
        if missing:
            raise BenchmarkConfigurationError(
                f"Selected tau2 tasks are absent from the native runtime: {missing}"
            )
        for task_id in self._records:
            self._validate_native_task(task_id)
        self._adapter = CallableSubjectAdapter(
            self._identity,
            self._execute,
            recorder_factory=self._recorder,
        )

    @classmethod
    def from_installed(
        cls,
        identity: SubjectIdentity,
        benchmark_manifest: BenchmarkManifest,
        simulation_factory: Tau2SimulationFactory,
    ) -> "Tau2SubjectAdapter":
        """Bind an installed tau2 checkout after version and task-byte checks."""

        domain = benchmark_manifest.suite
        if not domain.isidentifier():
            raise BenchmarkConfigurationError(f"Invalid tau2 domain {domain!r}")
        try:
            installed_version = metadata.version(TAU2_DISTRIBUTION)
            domain_module = import_module(f"tau2.domains.{domain}.environment")
            evaluator_module = import_module("tau2.evaluator.evaluator_env")
            utils_module = import_module(f"tau2.domains.{domain}.utils")
        except (metadata.PackageNotFoundError, ImportError) as exc:
            raise Tau2UnavailableError(
                "Install the pinned tau2 Git checkout in Python 3.12 to execute "
                "native scorer-bound tasks"
            ) from exc
        if installed_version != benchmark_manifest.source.package_version:
            raise BenchmarkConfigurationError(
                "Installed tau2 version does not match Benchmark Manifest"
            )
        task_path = getattr(utils_module, f"{domain.upper()}_TASK_SET_PATH", None)
        if task_path is None:
            raise BenchmarkConfigurationError(
                f"tau2 domain {domain!r} does not expose a task-set path"
            )
        actual_tasks_sha256 = hashlib.sha256(Path(task_path).read_bytes()).hexdigest()
        expected_tasks_sha256 = benchmark_manifest.metadata.get("tasks_sha256")
        if actual_tasks_sha256 != expected_tasks_sha256:
            raise BenchmarkConfigurationError(
                "Installed tau2 task bytes do not match Benchmark Manifest"
            )
        installed_source = BenchmarkSourceIdentity(
            benchmark_id=benchmark_manifest.source.benchmark_id,
            distribution=TAU2_DISTRIBUTION,
            package_version=installed_version,
            revision=benchmark_manifest.source.revision,
            source_url=benchmark_manifest.source.source_url,
            license=benchmark_manifest.source.license,
            metadata=benchmark_manifest.source.metadata,
        )
        return cls(
            identity,
            benchmark_manifest,
            simulation_factory,
            lambda: domain_module.get_tasks(None),
            domain_module.get_environment,
            evaluator_module.EnvironmentEvaluator,
            installed_source=installed_source,
        )

    @property
    def identity(self) -> SubjectIdentity:
        return SubjectIdentity.model_validate(self._identity.model_dump())

    @property
    def benchmark_manifest(self) -> BenchmarkManifest:
        return BenchmarkManifest.model_validate(self._manifest.model_dump())

    def scenario_for(
        self,
        source_task_id: str,
        *,
        seed: Optional[int] = None,
    ) -> Scenario:
        record = self._record_for(source_task_id)
        task = self._native_tasks[source_task_id]
        scope = self._state_scope()
        pre_state, gold_state = self._pre_and_gold_states(task)
        expected_changes = _state_delta(pre_state, gold_state)
        expected_effects = {
            _effect_id(scope, path): 1 for path in expected_changes
        }

        scenario = Scenario.model_validate(record.scenario.model_dump())
        benchmark = dict(scenario.metadata["benchmark"])
        benchmark["oracle_binding"] = TAU2_NATIVE_BINDING
        benchmark["binding"] = {
            "source_manifest_id": self._manifest.manifest_id,
            "source_manifest_digest": self._manifest.manifest_digest,
            "utility_callable": record.scorer.utility_callable,
            "trace_callable": record.scorer.trace_callable,
            "deterministic_view": "T0-DB",
            "strict_replay": True,
            "state_scope": scope,
            "reference_action_semantics": "target_state_derivation_only",
        }
        scenario.metadata["benchmark"] = benchmark
        scenario.fixtures["tau2_t2"] = {
            "state_scope": scope,
            "expected_state_delta": expected_changes,
            "expected_side_effects": expected_effects,
            "pre_state_digest": canonical_value_digest(pre_state),
            "gold_state_digest": canonical_value_digest(gold_state),
            "side_effect_semantics": "changed_state_path_exactly_once",
        }
        scenario.seed = seed
        scenario.oracles = [
            OracleSpec(
                oracle_id="tau2-native-t0-db",
                kind=OracleKind.GOAL_ACHIEVEMENT,
                parameters={"expected": True},
            ),
            OracleSpec(
                oracle_id="tau2-task-derived-state-delta",
                kind=OracleKind.STATE_DELTA,
                parameters={
                    "scope": scope,
                    "expected_changes": expected_changes,
                    "allow_unexpected_changes": False,
                },
            ),
            OracleSpec(
                oracle_id="tau2-exactly-once-side-effects",
                kind=OracleKind.SIDE_EFFECTS,
                parameters={
                    "expected_attempts": expected_effects,
                    "expected_commits": expected_effects,
                    "allow_unexpected_effects": False,
                    "require_unique_attempt_ids": True,
                },
            ),
        ]
        return scenario

    def run(self, scenario: Scenario) -> ScenarioReport:
        return self._adapter.run(scenario)

    def _execute(self, scenario: Scenario, recorder: TraceRecorder) -> RunResult:
        task_id = self._task_id_from(scenario)
        expected = self.scenario_for(task_id, seed=scenario.seed)
        if scenario != expected:
            raise BenchmarkConfigurationError(
                "Execution Scenario does not match its native scorer-bound "
                "tau2 record"
            )
        task = self._native_tasks[task_id]
        simulation = self._simulation_factory(scenario, task, recorder)
        messages = getattr(simulation, "messages", None)
        if messages is None:
            raise BenchmarkConfigurationError(
                "tau2 native bridge currently requires half-duplex messages"
            )
        simulation_task_id = str(getattr(simulation, "task_id", ""))
        if simulation_task_id != task_id:
            raise BenchmarkConfigurationError(
                f"tau2 SimulationRun task {simulation_task_id!r} does not match "
                f"Scenario task {task_id!r}"
            )

        termination = _enum_value(getattr(simulation, "termination_reason", None))
        completed = termination in {"agent_stop", "user_stop"}
        reward_info = self._environment_evaluator.calculate_reward(
            environment_constructor=self._environment_constructor,
            task=task,
            full_trajectory=list(messages),
            solo_mode=False,
            env_kwargs={},
            strict_replay=True,
        )
        db_check = getattr(reward_info, "db_check", None)
        db_reward = (
            float(db_check.db_reward)
            if db_check is not None
            else float(getattr(reward_info, "reward", 0.0))
        )
        achieved = completed and db_reward == 1.0

        self._record_trajectory(messages, recorder)
        pre_state = self._initialized_state(task)
        predicted_state = self._replay_state(task, messages)
        actual_changes = _state_delta(pre_state, predicted_state)
        recorder.record_environment_delta(self._state_scope(), actual_changes)
        self._record_side_effect_ledger(task, messages, recorder)

        pre_digest = canonical_value_digest(pre_state)
        post_digest = canonical_value_digest(predicted_state)
        recorder.record(
            TraceEventType.CUSTOM,
            {
                "adapter": "tau2",
                "adapter_event": "native_db_scored",
                "source_task_id": task_id,
                "termination_reason": termination,
                "completed": completed,
                "t0_view": "T0-DB",
                "db_reward": db_reward,
                "native_reward": float(getattr(reward_info, "reward", 0.0)),
                "reward_info": normalize_trace_value(reward_info),
                "pre_state_digest": pre_digest,
                "post_state_digest": post_digest,
                "strict_replay": True,
            },
        )
        return RunResult(
            status=RunStatus.COMPLETED if completed else RunStatus.STOPPED,
            stop_reason=(StopReason.SUBJECT_COMPLETED if completed else StopReason.ERROR),
            stop_detail=f"tau2:{termination}",
            final_answer=_last_assistant_content(messages),
            evaluation=EvaluationResult(
                achieved=achieved,
                confidence=1.0,
                explanation="Native tau2 deterministic DB evaluator (T0-DB)",
                evidence=[
                    f"benchmark_manifest_digest={self._manifest.manifest_digest}",
                    f"source_task_id={task_id}",
                    f"termination_reason={termination}",
                    f"db_reward={db_reward}",
                    f"pre_state_digest={pre_digest}",
                    f"post_state_digest={post_digest}",
                ],
            ),
        )

    def _record_trajectory(self, messages: Sequence[Any], recorder: TraceRecorder) -> None:
        for index, message in enumerate(messages):
            role = getattr(message, "role", None)
            if role in {"assistant", "user"}:
                recorder.record(
                    TraceEventType.MODEL_EXCHANGE,
                    {
                        "adapter": "tau2",
                        "message_index": index,
                        "participant": role,
                        "response": normalize_trace_value(message),
                    },
                )
        for index, (call, result) in enumerate(_tool_attempts(messages)):
            payload = {
                "adapter": "tau2",
                "attempt_index": index,
                "name": str(getattr(call, "name", "")),
                "arguments": normalize_trace_value(
                    getattr(call, "arguments", {}) or {}
                ),
                "call_id": str(getattr(call, "id", "")),
                "requestor": str(getattr(call, "requestor", "assistant")),
                "result": getattr(result, "content", None),
                "error": getattr(result, "error", None),
                "attempt_count": 1,
                "delivery_source": "tau2_simulation_run",
            }
            event_type = (
                TraceEventType.TOOL_ERROR
                if getattr(result, "error", False)
                else TraceEventType.TOOL_EXCHANGE
            )
            recorder.record(event_type, payload)

    def _record_side_effect_ledger(
        self,
        task: Any,
        messages: Sequence[Any],
        recorder: TraceRecorder,
    ) -> None:
        environment = self._initialized_environment(task)
        scope = self._state_scope()
        known_paths: dict[str, list[str]] = {}
        execution_messages = _execution_messages(task, messages)
        for index, (call, result) in enumerate(_tool_attempts(execution_messages)):
            name = str(getattr(call, "name", ""))
            requestor = str(getattr(call, "requestor", "assistant"))
            arguments = normalize_trace_value(getattr(call, "arguments", {}) or {})
            if not _tool_mutates(environment, name, requestor):
                continue
            before = _environment_state(environment)
            outcome = "committed"
            try:
                environment.make_tool_call(
                    tool_name=name,
                    requestor=requestor,
                    **dict(arguments),
                )
                if getattr(result, "error", False):
                    outcome = "failed"
            except Exception:
                outcome = "failed"
            after = _environment_state(environment)
            changed_paths = sorted(_state_delta(before, after))
            signature = _call_signature(name, requestor, arguments)
            if changed_paths:
                known_paths[signature] = changed_paths
            else:
                changed_paths = known_paths.get(
                    signature,
                    [f"/no_state_change/{name}/{signature[:12]}"],
                )
            native_call_id = str(getattr(call, "id", "")) or f"sequence-{index}"
            for path in changed_paths:
                path_digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:12]
                recorder.record_side_effect(
                    _effect_id(scope, path),
                    f"{native_call_id}:{path_digest}",
                    outcome=outcome,
                    attributes={
                        "native_call_id": str(getattr(call, "id", "")),
                        "tool_name": name,
                        "requestor": requestor,
                        "arguments": arguments,
                        "state_path": path,
                        "source": "tau2_simulation_trajectory",
                    },
                )

    def _pre_and_gold_states(self, task: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        pre_environment = self._initialized_environment(task)
        gold_environment = self._initialized_environment(task)
        criteria = getattr(task, "evaluation_criteria", None)
        for action in (getattr(criteria, "actions", None) or []):
            gold_environment.make_tool_call(
                tool_name=action.name,
                requestor=action.requestor,
                **dict(action.arguments),
            )
        return (
            _environment_state(pre_environment),
            _environment_state(gold_environment),
        )

    def _initialized_environment(self, task: Any) -> Any:
        environment = self._environment_constructor(solo_mode=False)
        initial = getattr(task, "initial_state", None)
        environment.set_state(
            initialization_data=(
                getattr(initial, "initialization_data", None) if initial else None
            ),
            initialization_actions=(
                getattr(initial, "initialization_actions", None) if initial else None
            ),
            message_history=list(
                getattr(initial, "message_history", None) or [] if initial else []
            ),
            strict=True,
        )
        return environment

    def _initialized_state(self, task: Any) -> dict[str, Any]:
        return _environment_state(self._initialized_environment(task))

    def _replay_state(self, task: Any, messages: Sequence[Any]) -> dict[str, Any]:
        environment = self._environment_constructor(solo_mode=False)
        initial = getattr(task, "initial_state", None)
        environment.set_state(
            initialization_data=(
                getattr(initial, "initialization_data", None) if initial else None
            ),
            initialization_actions=(
                getattr(initial, "initialization_actions", None) if initial else None
            ),
            message_history=list(messages),
            strict=True,
        )
        return _environment_state(environment)

    def _validate_native_task(self, task_id: str) -> None:
        record = self._records[task_id]
        task = self._native_tasks[task_id]
        native = normalize_trace_value(task)
        scenario = record.scenario
        fixture = scenario.fixtures["tau2_task"]
        expected_reference = scenario.fixtures["tau2_reference"]["actions"]
        criteria = native.get("evaluation_criteria") or {}
        comparisons = {
            "user_scenario": (
                _strip_none(native.get("user_scenario")),
                _strip_none(fixture.get("user_scenario")),
            ),
            "initial_state": (
                _strip_none(native.get("initial_state")),
                _strip_none(fixture.get("initial_state")),
            ),
            "description": (
                _strip_none(native.get("description")),
                _strip_none(fixture.get("description")),
            ),
            "reference_actions": (
                [_canonical_action(action) for action in criteria.get("actions") or []],
                [_canonical_action(action) for action in expected_reference],
            ),
            "reward_basis": (
                criteria.get("reward_basis") or [],
                scenario.fixtures["tau2_reward"]["reward_basis"],
            ),
            "nl_assertions": (
                criteria.get("nl_assertions") or [],
                scenario.fixtures["tau2_reward"]["nl_assertions"],
            ),
        }
        drift = [name for name, values in comparisons.items() if values[0] != values[1]]
        if drift:
            raise BenchmarkConfigurationError(
                f"Native tau2 task {task_id!r} drifted in fields {drift}"
            )

    def _record_for(self, source_task_id: str):
        try:
            return self._records[source_task_id]
        except KeyError as exc:
            raise BenchmarkConfigurationError(
                f"Task {source_task_id!r} is absent from Benchmark Manifest"
            ) from exc

    def _task_id_from(self, scenario: Scenario) -> str:
        benchmark = scenario.metadata.get("benchmark")
        task_id = benchmark.get("source_task_id") if isinstance(benchmark, dict) else None
        if not isinstance(task_id, str) or task_id not in self._records:
            raise BenchmarkConfigurationError(
                "Scenario lacks a selected tau2 source_task_id"
            )
        return task_id

    def _state_scope(self) -> str:
        return f"tau2:{self._manifest.suite}:db"

    def _recorder(self, scenario: Scenario, identity: SubjectIdentity) -> TraceRecorder:
        return TraceRecorder(
            metadata={
                "scenario_id": scenario.scenario_id,
                "seed": scenario.seed,
                "tags": scenario.tags,
                "subject": identity.model_dump(mode="json"),
                "benchmark_manifest_digest": self._manifest.manifest_digest,
                "adapter": "tau2-native",
            }
        )


def _environment_state(environment: Any) -> dict[str, Any]:
    def toolkit_state(toolkit: Any) -> Any:
        db = getattr(toolkit, "db", None) if toolkit is not None else None
        return normalize_trace_value(db) if db is not None else None

    return {
        "assistant": toolkit_state(getattr(environment, "tools", None)),
        "user": toolkit_state(getattr(environment, "user_tools", None)),
    }


def _state_delta(before: Any, after: Any) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    _collect_changes(before, after, "", changes)
    return changes


def _collect_changes(before: Any, after: Any, path: str, changes: dict[str, Any]) -> None:
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        for key in sorted(set(before) | set(after), key=str):
            child = f"{path}/{_json_pointer_token(str(key))}"
            if key not in before:
                _collect_added(after[key], child, changes)
            elif key not in after:
                _collect_removed(before[key], child, changes)
            else:
                _collect_changes(before[key], after[key], child, changes)
        return
    if isinstance(before, list) and isinstance(after, list):
        for index in range(max(len(before), len(after))):
            child = f"{path}/{index}"
            if index >= len(before):
                _collect_added(after[index], child, changes)
            elif index >= len(after):
                _collect_removed(before[index], child, changes)
            else:
                _collect_changes(before[index], after[index], child, changes)
        return
    if before != after:
        changes[path or "/"] = {
            "operation": "replace",
            "before": normalize_trace_value(before),
            "after": normalize_trace_value(after),
        }


def _collect_added(value: Any, path: str, changes: dict[str, Any]) -> None:
    if isinstance(value, Mapping):
        if not value:
            changes[path] = {"operation": "add", "before": None, "after": {}}
        for key in sorted(value, key=str):
            _collect_added(value[key], f"{path}/{_json_pointer_token(str(key))}", changes)
        return
    if isinstance(value, list):
        if not value:
            changes[path] = {"operation": "add", "before": None, "after": []}
        for index, item in enumerate(value):
            _collect_added(item, f"{path}/{index}", changes)
        return
    changes[path] = {
        "operation": "add",
        "before": None,
        "after": normalize_trace_value(value),
    }


def _collect_removed(value: Any, path: str, changes: dict[str, Any]) -> None:
    if isinstance(value, Mapping):
        if not value:
            changes[path] = {"operation": "remove", "before": {}, "after": None}
        for key in sorted(value, key=str):
            _collect_removed(
                value[key], f"{path}/{_json_pointer_token(str(key))}", changes
            )
        return
    if isinstance(value, list):
        if not value:
            changes[path] = {"operation": "remove", "before": [], "after": None}
        for index, item in enumerate(value):
            _collect_removed(item, f"{path}/{index}", changes)
        return
    changes[path] = {
        "operation": "remove",
        "before": normalize_trace_value(value),
        "after": None,
    }


def _json_pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _tool_attempts(messages: Sequence[Any]) -> list[tuple[Any, Any]]:
    pending: list[Any] = []
    attempts = []
    for message in messages:
        role = getattr(message, "role", None)
        if role in {"assistant", "user"}:
            pending.extend(list(getattr(message, "tool_calls", None) or []))
        elif role == "tool":
            result_id = str(getattr(message, "id", ""))
            match_index = next(
                (
                    index
                    for index, call in enumerate(pending)
                    if str(getattr(call, "id", "")) == result_id
                ),
                None,
            )
            if match_index is None:
                raise BenchmarkConfigurationError(
                    f"tau2 ToolMessage {result_id!r} has no preceding ToolCall"
                )
            attempts.append((pending.pop(match_index), message))
    if pending:
        missing = [str(getattr(call, "id", "")) for call in pending]
        raise BenchmarkConfigurationError(
            f"tau2 ToolCalls lack ToolMessage results: {missing}"
        )
    return attempts


def _tool_mutates(environment: Any, name: str, requestor: str) -> bool:
    toolkit = (
        getattr(environment, "user_tools", None)
        if requestor == "user"
        else getattr(environment, "tools", None)
    )
    if toolkit is None or not toolkit.has_tool(name):
        return True
    return bool(toolkit.tool_mutates_state(name))


def _effect_id(scope: str, path: str) -> str:
    return f"{scope}:{path}"


def _call_signature(name: str, requestor: str, arguments: Any) -> str:
    value = json.dumps(
        {"name": name, "requestor": requestor, "arguments": arguments},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _enum_value(value: Any) -> Optional[str]:
    return getattr(value, "value", value) if value is not None else None


def _last_assistant_content(messages: Sequence[Any]) -> Optional[str]:
    for message in reversed(messages):
        if getattr(message, "role", None) == "assistant":
            content = getattr(message, "content", None)
            if content is not None:
                return str(content)
    return None


def _execution_messages(task: Any, messages: Sequence[Any]) -> list[Any]:
    initial = getattr(task, "initial_state", None)
    initial_messages = list(
        getattr(initial, "message_history", None) or [] if initial else []
    )
    if not initial_messages:
        return list(messages)
    prefix = list(messages[: len(initial_messages)])
    if normalize_trace_value(prefix) != normalize_trace_value(initial_messages):
        raise BenchmarkConfigurationError(
            "tau2 SimulationRun does not retain the task initial message prefix"
        )
    return list(messages[len(initial_messages) :])


def _strip_none(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _strip_none(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [_strip_none(item) for item in value]
    return value


def _canonical_action(action: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "action_id": str(action.get("action_id", "")),
        "requestor": str(action.get("requestor", "assistant")),
        "name": str(action.get("name", "")),
        "arguments": normalize_trace_value(action.get("arguments") or {}),
    }
