"""Optional external benchmark converters with frozen provenance."""

from nanoharness.testing.benchmarks.base import (
    BENCHMARK_MANIFEST_SCHEMA_VERSION,
    BenchmarkConfigurationError,
    BenchmarkManifest,
    BenchmarkScenarioRecord,
    BenchmarkScorerProvenance,
    BenchmarkSourceIdentity,
    benchmark_manifest_digest,
    canonical_value_digest,
)
from nanoharness.testing.benchmarks.agentdojo import (
    AGENTDOJO_BENCHMARK_VERSION,
    AGENTDOJO_PACKAGE_VERSION,
    AGENTDOJO_REVISION,
    AgentDojoBenchmarkAdapter,
    AgentDojoUnavailableError,
)
from nanoharness.testing.benchmarks.tau2 import (
    TAU2_BENCHMARK_VERSION,
    TAU2_PACKAGE_VERSION,
    TAU2_RETAIL_TASKS_SHA256,
    TAU2_REVISION,
    TAU2_TAG_OBJECT,
    Tau2BenchmarkAdapter,
)

__all__ = [
    "BENCHMARK_MANIFEST_SCHEMA_VERSION",
    "BenchmarkConfigurationError",
    "BenchmarkManifest",
    "BenchmarkScenarioRecord",
    "BenchmarkScorerProvenance",
    "BenchmarkSourceIdentity",
    "benchmark_manifest_digest",
    "canonical_value_digest",
    "AGENTDOJO_BENCHMARK_VERSION",
    "AGENTDOJO_PACKAGE_VERSION",
    "AGENTDOJO_REVISION",
    "AgentDojoBenchmarkAdapter",
    "AgentDojoUnavailableError",
    "TAU2_BENCHMARK_VERSION",
    "TAU2_PACKAGE_VERSION",
    "TAU2_RETAIL_TASKS_SHA256",
    "TAU2_REVISION",
    "TAU2_TAG_OBJECT",
    "Tau2BenchmarkAdapter",
]
