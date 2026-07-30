"""Scorer-bound execution bridge for pinned AgentDojo user tasks."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from importlib import import_module, metadata
from typing import Any, Optional, Protocol

from nanoharness.core.schema import (
    EvaluationResult,
    RunResult,
    RunStatus,
    StopReason,
)
from nanoharness.testing.adapters.base import (
    CallableSubjectAdapter,
    SubjectIdentity,
)
from nanoharness.testing.benchmarks.agentdojo import (
    AGENTDOJO_DISTRIBUTION,
    AgentDojoUnavailableError,
)
from nanoharness.testing.benchmarks.base import (
    BenchmarkConfigurationError,
    BenchmarkManifest,
    BenchmarkSourceIdentity,
    canonical_value_digest,
)
from nanoharness.testing.scenario import (
    OracleKind,
    OracleSpec,
    Scenario,
    ScenarioReport,
)
from nanoharness.testing.trace import (
    TraceEventType,
    TraceRecorder,
    normalize_trace_value,
)


AGENTDOJO_UTILITY_BINDING = "agentdojo_user_utility"


class AgentDojoPipelineFactory(Protocol):
    """Create a fresh AgentDojo pipeline for one bound task execution."""

    def __call__(self, scenario: Scenario, task: Any) -> Any:
        ...


class AgentDojoRuntimeFactory(Protocol):
    """Create a fresh AgentDojo FunctionsRuntime with live instrumentation."""

    def __call__(self, tools: Sequence[Any], recorder: TraceRecorder) -> Any:
        ...


class AgentDojoSubjectAdapter:
    """Execute converted user tasks with their original AgentDojo utility.

    The input Benchmark Manifest remains an immutable, unbound conversion
    artifact. ``scenario_for`` creates a derived execution Scenario whose sole
    success Oracle checks the original utility result returned by this bridge.
    Runtime-specific environments never become NanoHarness state objects.
    """

    def __init__(
        self,
        identity: SubjectIdentity,
        benchmark_manifest: BenchmarkManifest,
        pipeline_factory: AgentDojoPipelineFactory,
        suite_loader: Callable[[str, str], Any],
        runtime_factory: AgentDojoRuntimeFactory,
        abort_error_type: type[BaseException],
        text_extractor: Callable[[Any], str],
        *,
        installed_source: Optional[BenchmarkSourceIdentity] = None,
        max_attempts: int = 3,
    ):
        if identity.runtime.casefold() != "agentdojo":
            raise ValueError("AgentDojo subject identity runtime must be 'agentdojo'")
        if max_attempts < 1:
            raise ValueError("AgentDojo max_attempts must be at least one")
        benchmark_manifest.assert_unchanged()
        if benchmark_manifest.oracle_binding != "unbound":
            raise BenchmarkConfigurationError(
                "AgentDojo source Manifest must remain scorer-unbound"
            )
        if installed_source is not None and benchmark_manifest.source != installed_source:
            raise BenchmarkConfigurationError(
                "AgentDojo installed source does not match Benchmark Manifest"
            )
        self._identity = SubjectIdentity.model_validate(identity.model_dump())
        self._manifest = BenchmarkManifest.model_validate(
            benchmark_manifest.model_dump()
        )
        self._pipeline_factory = pipeline_factory
        self._suite_loader = suite_loader
        self._runtime_factory = runtime_factory
        self._abort_error_type = abort_error_type
        self._text_extractor = text_extractor
        self._max_attempts = max_attempts
        self._records = {
            record.source_task_id: record for record in self._manifest.tasks
        }
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
        pipeline_factory: AgentDojoPipelineFactory,
        *,
        max_attempts: int = 3,
    ) -> "AgentDojoSubjectAdapter":
        try:
            installed_version = metadata.version(AGENTDOJO_DISTRIBUTION)
            load_module = import_module("agentdojo.task_suite.load_suites")
            runtime_module = import_module("agentdojo.functions_runtime")
            error_module = import_module("agentdojo.agent_pipeline.errors")
            type_module = import_module("agentdojo.types")
        except (metadata.PackageNotFoundError, ImportError) as exc:
            raise AgentDojoUnavailableError(
                "Install the pinned AgentDojo research dependency to execute "
                "scorer-bound tasks"
            ) from exc
        if installed_version != benchmark_manifest.source.package_version:
            raise BenchmarkConfigurationError(
                "Installed AgentDojo version does not match Benchmark Manifest"
            )

        runtime_class = runtime_module.FunctionsRuntime

        def runtime_factory(tools, recorder):
            class RecordingFunctionsRuntime(runtime_class):
                def run_function(
                    self,
                    env,
                    function,
                    kwargs,
                    raise_on_error=False,
                ):
                    arguments = normalize_trace_value(kwargs)
                    recorder.record(
                        TraceEventType.TOOL_STARTED,
                        {"name": function, "arguments": arguments},
                    )
                    try:
                        result, error = super().run_function(
                            env,
                            function,
                            kwargs,
                            raise_on_error=raise_on_error,
                        )
                    except Exception as exc:
                        recorder.record(
                            TraceEventType.TOOL_ERROR,
                            {
                                "name": function,
                                "arguments": arguments,
                                "error_type": type(exc).__name__,
                                "message": str(exc),
                                "attempt_count": 1,
                            },
                        )
                        raise
                    event_type = (
                        TraceEventType.TOOL_ERROR
                        if error is not None
                        else TraceEventType.TOOL_COMPLETED
                    )
                    recorder.record(
                        event_type,
                        {
                            "name": function,
                            "arguments": arguments,
                            "result": result,
                            "error": error,
                            "attempt_count": 1,
                        },
                    )
                    return result, error

            return RecordingFunctionsRuntime(tools)

        installed_source = BenchmarkSourceIdentity(
            benchmark_id=f"agentdojo@pypi-{installed_version}",
            distribution=AGENTDOJO_DISTRIBUTION,
            package_version=installed_version,
            revision=benchmark_manifest.source.revision,
            source_url=benchmark_manifest.source.source_url,
            license=benchmark_manifest.source.license,
            metadata=benchmark_manifest.source.metadata,
        )
        return cls(
            identity,
            benchmark_manifest,
            pipeline_factory,
            load_module.get_suite,
            runtime_factory,
            error_module.AbortAgentError,
            type_module.get_text_content_as_str,
            installed_source=installed_source,
            max_attempts=max_attempts,
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
        try:
            record = self._records[source_task_id]
        except KeyError as exc:
            raise BenchmarkConfigurationError(
                f"Task {source_task_id!r} is absent from Benchmark Manifest"
            ) from exc
        scenario = Scenario.model_validate(record.scenario.model_dump())
        benchmark = dict(scenario.metadata["benchmark"])
        benchmark["oracle_binding"] = AGENTDOJO_UTILITY_BINDING
        benchmark["binding"] = {
            "source_manifest_id": self._manifest.manifest_id,
            "source_manifest_digest": self._manifest.manifest_digest,
            "utility_callable": record.scorer.utility_callable,
            "trace_callable": record.scorer.trace_callable,
            "trace_aware": record.scorer.trace_aware,
            "strict": True,
            "scorer_trace_scope": "final_attempt",
        }
        scenario.metadata["benchmark"] = benchmark
        scenario.seed = seed
        scenario.oracles = [
            OracleSpec(
                oracle_id="agentdojo-original-utility",
                kind=OracleKind.GOAL_ACHIEVEMENT,
                parameters={"expected": True},
            )
        ]
        return scenario

    def run(self, scenario: Scenario) -> ScenarioReport:
        self._manifest.assert_unchanged()
        task_id = self._task_id_from(scenario)
        expected = self.scenario_for(task_id, seed=scenario.seed)
        if scenario != expected:
            raise BenchmarkConfigurationError(
                "Execution Scenario does not match its scorer-bound Benchmark "
                "record"
            )
        return self._adapter.run(scenario)

    def _task_id_from(self, scenario: Scenario) -> str:
        benchmark = scenario.metadata.get("benchmark")
        task_id = benchmark.get("source_task_id") if isinstance(benchmark, dict) else None
        if not isinstance(task_id, str) or task_id not in self._records:
            raise BenchmarkConfigurationError(
                "Scenario lacks a selected AgentDojo source_task_id"
            )
        return task_id

    def _recorder(
        self,
        scenario: Scenario,
        identity: SubjectIdentity,
    ) -> TraceRecorder:
        benchmark = scenario.metadata["benchmark"]
        return TraceRecorder(
            metadata={
                "scenario_id": scenario.scenario_id,
                "seed": scenario.seed,
                "tags": scenario.tags,
                "subject": identity.model_dump(mode="json"),
                "benchmark": benchmark,
                "benchmark_manifest_digest": self._manifest.manifest_digest,
                "seed_controlled_sources": [],
            }
        )

    def _execute(self, scenario: Scenario, recorder: TraceRecorder) -> RunResult:
        task_id = self._task_id_from(scenario)
        record = self._records[task_id]
        task_suite = self._suite_loader(
            self._manifest.benchmark_version,
            self._manifest.suite,
        )
        task = task_suite.user_tasks[task_id]
        environment = task_suite.load_and_inject_default_environment({})
        task_environment = task.init_environment(environment)
        pre_environment = task_environment.model_copy(deep=True)
        actual_pre_digest = canonical_value_digest(
            pre_environment.model_dump(mode="json")
        )
        if actual_pre_digest != record.pre_environment_digest:
            raise BenchmarkConfigurationError(
                f"AgentDojo pre-environment drift for task {task_id!r}"
            )
        recorder.record(
            TraceEventType.CUSTOM,
            {
                "adapter": "agentdojo",
                "adapter_event": "pre_environment",
                "digest": actual_pre_digest,
                "environment_type": _qualified_name(type(pre_environment)),
            },
        )
        recorder.record(
            TraceEventType.TOOL_SCHEMAS,
            {
                "tools": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters.model_json_schema(),
                    }
                    for tool in task_suite.tools
                ]
            },
        )
        runtime = self._runtime_factory(task_suite.tools, recorder)
        pipeline = self._pipeline_factory(scenario, task)
        messages: Sequence[Any] = []
        model_output: Optional[str] = None
        attempts = 0
        for attempt in range(self._max_attempts):
            attempts = attempt + 1
            try:
                _, runtime, task_environment, messages, _ = pipeline.query(
                    task.PROMPT,
                    runtime,
                    task_environment,
                )
            except self._abort_error_type as exc:
                task_environment = exc.task_environment
                messages = exc.messages
            self._record_messages(messages, recorder, attempt=attempts)
            model_output = self._model_output(messages)
            if model_output is not None:
                break

        function_trace = _function_trace(messages)
        trace_score = task.utility_from_traces(
            model_output or "",
            pre_environment,
            task_environment,
            function_trace,
        )
        if trace_score is None:
            achieved = task.utility(
                model_output or "",
                pre_environment,
                task_environment,
                strict=True,
            )
            scorer_path = "utility"
        else:
            achieved = trace_score
            scorer_path = "utility_from_traces"
        if not isinstance(achieved, bool):
            raise TypeError("AgentDojo utility scorer must return bool")
        post_digest = canonical_value_digest(
            task_environment.model_dump(mode="json")
        )
        recorder.record(
            TraceEventType.CUSTOM,
            {
                "adapter": "agentdojo",
                "adapter_event": "utility_scored",
                "achieved": achieved,
                "scorer_path": scorer_path,
                "attempts": attempts,
                "scorer_trace_scope": "final_attempt",
                "pre_environment_digest": actual_pre_digest,
                "post_environment_digest": post_digest,
                "function_trace": function_trace,
            },
        )
        return RunResult(
            status=RunStatus.COMPLETED,
            stop_reason=StopReason.SUBJECT_COMPLETED,
            final_answer=model_output,
            evaluation=EvaluationResult(
                achieved=achieved,
                confidence=1.0,
                explanation="Original AgentDojo user-task utility scorer",
                evidence=[
                    f"benchmark_manifest_digest={self._manifest.manifest_digest}",
                    f"source_task_id={task_id}",
                    f"scorer_path={scorer_path}",
                    f"attempts={attempts}",
                    f"pre_environment_digest={actual_pre_digest}",
                    f"post_environment_digest={post_digest}",
                ],
            ),
        )

    def _model_output(self, messages: Sequence[Any]) -> Optional[str]:
        if not messages or messages[-1].get("role") != "assistant":
            return None
        content = messages[-1].get("content")
        if content is None:
            return None
        return self._text_extractor(content)

    def _record_messages(
        self,
        messages: Sequence[Any],
        recorder: TraceRecorder,
        *,
        attempt: int,
    ) -> None:
        prefix = []
        for index, message in enumerate(messages):
            normalized = normalize_trace_value(message)
            prefix.append(normalized)
            role = message.get("role")
            if role == "assistant":
                recorder.record(
                    TraceEventType.MODEL_EXCHANGE,
                    {
                        "adapter": "agentdojo",
                        "attempt": attempt,
                        "message_index": index,
                        "messages": list(prefix),
                        "response": normalized,
                    },
                )
            elif role == "tool":
                call = message.get("tool_call")
                call_value = normalize_trace_value(call)
                name = call_value.get("function") if isinstance(call_value, dict) else None
                arguments = call_value.get("args", {}) if isinstance(call_value, dict) else {}
                content = message.get("content")
                delivered = self._text_extractor(content) if content is not None else ""
                error = message.get("error")
                payload = {
                    "adapter": "agentdojo",
                    "attempt": attempt,
                    "message_index": index,
                    "name": name,
                    "arguments": arguments,
                    "result": delivered,
                    "error": error,
                    "attempt_count": 1,
                    "delivery_source": "agentdojo_message",
                }
                if error is None:
                    recorder.record(TraceEventType.TOOL_EXCHANGE, payload)
                else:
                    recorder.record(
                        TraceEventType.CUSTOM,
                        {
                            **payload,
                            "adapter_event": "tool_error_delivered",
                        },
                    )


def _function_trace(messages: Sequence[Any]) -> list[Any]:
    calls = []
    for message in messages:
        if message.get("role") == "assistant":
            calls.extend(message.get("tool_calls") or [])
    return calls


def _qualified_name(value: Any) -> str:
    return f"{value.__module__}.{value.__qualname__}"
