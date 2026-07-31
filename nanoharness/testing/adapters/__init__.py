"""Optional subject adapters that preserve NanoHarness testing contracts."""

from nanoharness.testing.adapters.base import (
    CallableSubjectAdapter,
    ExternalExecutor,
    ScenarioRunnerAdapter,
    SubjectAdapter,
    SubjectIdentity,
)
from nanoharness.testing.adapters.langgraph import (
    LangGraphConfigBuilder,
    LangGraphBuilder,
    LangGraphFactory,
    LangGraphInputBuilder,
    LangGraphResultMapper,
    LangGraphSubjectAdapter,
    LangGraphUnavailableError,
)
from nanoharness.testing.adapters.agentdojo import (
    AGENTDOJO_UTILITY_BINDING,
    AgentDojoFaultAdapterFactory,
    AgentDojoPipelineFactory,
    AgentDojoRuntimeFactory,
    AgentDojoSubjectAdapter,
)
from nanoharness.testing.adapters.tau2 import (
    TAU2_NATIVE_BINDING,
    Tau2SimulationFactory,
    Tau2SubjectAdapter,
    Tau2UnavailableError,
)

__all__ = [
    "CallableSubjectAdapter",
    "ExternalExecutor",
    "ScenarioRunnerAdapter",
    "SubjectAdapter",
    "SubjectIdentity",
    "LangGraphConfigBuilder",
    "LangGraphBuilder",
    "LangGraphFactory",
    "LangGraphInputBuilder",
    "LangGraphResultMapper",
    "LangGraphSubjectAdapter",
    "LangGraphUnavailableError",
    "AGENTDOJO_UTILITY_BINDING",
    "AgentDojoFaultAdapterFactory",
    "AgentDojoPipelineFactory",
    "AgentDojoRuntimeFactory",
    "AgentDojoSubjectAdapter",
    "TAU2_NATIVE_BINDING",
    "Tau2SimulationFactory",
    "Tau2SubjectAdapter",
    "Tau2UnavailableError",
]
