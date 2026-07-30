"""Optional LangGraph subject adapter using deterministic state-value streams."""

from __future__ import annotations

from importlib import metadata
from typing import Any, Dict, List, Optional, Protocol

from nanoharness.core.schema import RunResult
from nanoharness.testing.adapters.base import (
    CallableSubjectAdapter,
    SubjectIdentity,
)
from nanoharness.testing.scenario import Scenario, ScenarioReport
from nanoharness.testing.trace import TraceEventType, TraceRecorder


class LangGraphFactory(Protocol):
    def __call__(self) -> Any:
        ...


class LangGraphBuilder(Protocol):
    def __call__(self, scenario: Scenario, recorder: TraceRecorder) -> Any:
        ...


class LangGraphInputBuilder(Protocol):
    def __call__(self, scenario: Scenario) -> Dict[str, Any]:
        ...


class LangGraphResultMapper(Protocol):
    def __call__(
        self,
        scenario: Scenario,
        final_state: Any,
        snapshots: List[Any],
    ) -> RunResult:
        ...


class LangGraphConfigBuilder(Protocol):
    def __call__(self, scenario: Scenario) -> Dict[str, Any]:
        ...


class LangGraphUnavailableError(ImportError):
    """Raised when installed-package provenance cannot be established."""


class LangGraphSubjectAdapter:
    """Execute a fresh compiled graph and normalize its value snapshots.

    The graph must expose ``stream(input, stream_mode="values")``. Streaming
    full values avoids reconstructing reducer semantics from partial updates.
    The caller owns the deterministic result mapper and its success evidence.
    """

    def __init__(
        self,
        identity: SubjectIdentity,
        graph_factory: Optional[LangGraphFactory],
        input_builder: LangGraphInputBuilder,
        result_mapper: LangGraphResultMapper,
        *,
        config_builder: Optional[LangGraphConfigBuilder] = None,
        graph_builder: Optional[LangGraphBuilder] = None,
    ):
        if identity.runtime.casefold() != "langgraph":
            raise ValueError("LangGraph adapter identity runtime must be 'langgraph'")
        if (graph_factory is None) == (graph_builder is None):
            raise ValueError(
                "Provide exactly one of graph_factory or graph_builder"
            )
        self._identity = SubjectIdentity.model_validate(identity.model_dump())
        self._graph_factory = graph_factory
        self._graph_builder = graph_builder
        self._input_builder = input_builder
        self._result_mapper = result_mapper
        self._config_builder = config_builder
        self._adapter = CallableSubjectAdapter(
            self._identity,
            self._execute,
        )

    @classmethod
    def from_installed(
        cls,
        graph_factory: LangGraphFactory,
        input_builder: LangGraphInputBuilder,
        result_mapper: LangGraphResultMapper,
        *,
        config_builder: Optional[LangGraphConfigBuilder] = None,
    ) -> "LangGraphSubjectAdapter":
        try:
            version = metadata.version("langgraph")
        except metadata.PackageNotFoundError as exc:
            raise LangGraphUnavailableError(
                "Install the optional 'langgraph' package to use this adapter"
            ) from exc
        identity = SubjectIdentity(
            subject_id=f"langgraph@pypi-{version}",
            runtime="langgraph",
            version=version,
            revision=f"pypi:{version}",
            source_url="https://github.com/langchain-ai/langgraph",
            independently_developed=True,
            metadata={"distribution": "langgraph"},
        )
        return cls(
            identity,
            graph_factory,
            input_builder,
            result_mapper,
            config_builder=config_builder,
        )

    @classmethod
    def from_installed_builder(
        cls,
        graph_builder: LangGraphBuilder,
        input_builder: LangGraphInputBuilder,
        result_mapper: LangGraphResultMapper,
        *,
        config_builder: Optional[LangGraphConfigBuilder] = None,
    ) -> "LangGraphSubjectAdapter":
        try:
            version = metadata.version("langgraph")
        except metadata.PackageNotFoundError as exc:
            raise LangGraphUnavailableError(
                "Install the optional 'langgraph' package to use this adapter"
            ) from exc
        identity = SubjectIdentity(
            subject_id=f"langgraph@pypi-{version}",
            runtime="langgraph",
            version=version,
            revision=f"pypi:{version}",
            source_url="https://github.com/langchain-ai/langgraph",
            independently_developed=True,
            metadata={"distribution": "langgraph"},
        )
        return cls(
            identity,
            None,
            input_builder,
            result_mapper,
            config_builder=config_builder,
            graph_builder=graph_builder,
        )

    @property
    def identity(self) -> SubjectIdentity:
        return SubjectIdentity.model_validate(self._identity.model_dump())

    def run(self, scenario: Scenario) -> ScenarioReport:
        return self._adapter.run(scenario)

    def _execute(self, scenario: Scenario, recorder: TraceRecorder) -> RunResult:
        graph = (
            self._graph_builder(scenario, recorder)
            if self._graph_builder is not None
            else self._graph_factory()
        )
        stream = getattr(graph, "stream", None)
        if not callable(stream):
            raise TypeError("LangGraph factory must return an object with stream()")
        input_state = self._input_builder(scenario)
        config = self._config_builder(scenario) if self._config_builder else None
        if config is None:
            iterator = stream(input_state, stream_mode="values")
        else:
            iterator = stream(input_state, config=config, stream_mode="values")
        snapshots = []
        for index, snapshot in enumerate(iterator):
            snapshots.append(snapshot)
            recorder.record(
                TraceEventType.CUSTOM,
                {
                    "adapter": "langgraph",
                    "adapter_event": "state_snapshot",
                    "snapshot_index": index,
                    "state": snapshot,
                },
            )
        if not snapshots:
            raise RuntimeError("LangGraph execution produced no state snapshots")
        return self._result_mapper(
            scenario,
            snapshots[-1],
            snapshots,
        )
