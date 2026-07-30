# NanoHarness Roadmap

[中文](ROADMAP_CN.md) | English | [Agent Testing](AGENT_TESTING.md)

NanoHarness evolves through problem-driven components rather than feature
accumulation. The kernel stays small and defines stable contracts; integrations
adapt those contracts to external projects.

## Design principles

1. **Problem before component.** Every component must name the failure mode or
   operational need it addresses.
2. **Ports before adapters.** Core protocols are vendor-neutral. Mem0, LiteLLM,
   Redis, and other projects are optional adapters.
3. **Canonical internal models.** Third-party response objects are converted at
   the boundary into NanoHarness models.
4. **Policy is separate from mechanism.** For example, a memory provider stores
   and searches; a memory policy decides what to recall or retain.
5. **Optional dependencies.** Integrations must not increase the mandatory
   dependency surface of the kernel.
6. **Contract-tested extensibility.** Official and community adapters should run
   the same conformance suite.

## Component map

| Problem | Component contract | Expected outcome | Candidate adapters |
|---|---|---|---|
| A stopped agent is mistaken for a successful one | `EvaluationResult` / `Evaluator` | One authoritative success verdict | DeepEval, Ragas, custom evaluators |
| Task identity and metadata are lost between components | `TaskContext` | Stable query, run ID, and metadata propagation | Application-defined metadata |
| Parallel tool calls disappear from the trace | `ToolExecution` | One auditable record per tool call | MCP and local tools |
| Permissions, hooks, retries, and execution are mixed into the engine | `ToolExecutor` | A composable execution pipeline | MCP, sandboxed and remote executors |
| Reusing an engine contaminates a new task | `AgentSession` | Explicit task isolation and continuation | In-memory, Redis, database sessions |
| A crashed run cannot resume | `CheckpointStore` | Recoverable context, trajectory, and step state | JSON, SQLite, Redis, PostgreSQL |
| Long-term memory is coupled to one implementation | `MemoryProvider` + `MemoryPolicy` | Replaceable storage and recall strategy | Mem0, Qdrant, Chroma, custom stores |
| Model APIs require application-specific glue | `LLMProvider` | Normalized model responses and capabilities | LiteLLM, OpenAI, Anthropic |
| Production runs are difficult to inspect | `TelemetryProvider` | Traces, metrics, token usage, and cost | OpenTelemetry, LangFuse |
| Agent failures are flaky and test adequacy is unknown | `Scenario`, `TestOracle`, `ReplayProvider`, `CoverageCollector` | Reproducible failures and measurable test quality | pytest, Hypothesis, Harbor, Inspect AI |

## Phase 1 — Correctness and contracts

Goal: make the synchronous single-agent kernel semantically reliable before
expanding its feature surface.

- Make goal evaluation the authoritative success result and distinguish
  `terminated`, `achieved`, `failed`, and `cancelled`.
- Define canonical `RunResult`, `RunStatus`, and `StopReason` models, including
  explicit max-step, budget, error, evaluator, and user-cancellation outcomes.
- Introduce `TaskContext` for query, run ID, metadata, and task-scoped signals.
- Define a vendor-neutral `ToolSpec` with correct schema generation for nested
  types and mandatory argument validation before execution.
- Introduce `ToolExecution` so every tool call has its own result, error, and
  timing information.
- Guarantee paired lifecycle events and component cleanup even when model,
  state, evaluator, or hook execution fails.
- Define explicit new-run and continuation semantics to prevent context and
  trajectory leakage between tasks.
- Publish contract tests for evaluator, context, state, and tool components.

## Phase 2 — Execution, sessions, and recovery

Goal: keep `NanoEngine` focused on orchestration while execution and persistence
remain replaceable.

- Add a `ToolExecutor` pipeline for validation, permission checks, pre/post
  hooks, timeouts, retries, cancellation, and result normalization.
- Introduce `AgentSession` as the serializable state of a run.
- Upgrade state storage to checkpoint and restore context, trajectory, current
  step, and component state.
- Add versioned checkpoint schemas and documented migration behavior.
- Provide reference JSON and SQLite checkpoint stores, followed by a Redis
  adapter.
- Introduce execution budgets for steps, duration, tokens, cost, tool calls,
  and child-agent concurrency.
- Classify tool side effects and support call IDs, idempotency keys, and replay
  protection during recovery.
- Define resource ownership, cleanup, and concurrency guarantees for shared and
  session-scoped components.
- Add asynchronous execution and streaming without changing synchronous
  component contracts unnecessarily.

## Phase 3 — Memory and ecosystem adapters

Goal: make external capabilities pluggable without coupling the kernel to any
single project.

- Define `Memory`, `MemoryProvider`, and `MemoryPolicy` contracts.
- Ship an in-memory reference implementation and adapter contract tests.
- Add a Mem0 adapter as the first long-term-memory integration.
- Add LiteLLM model routing and normalize provider capability discovery.
- Treat MCP as a tool adapter and normalize its schemas and results into
  NanoHarness models.
- Add optional extras such as `nanoharness[mem0]`, `nanoharness[litellm]`, and
  `nanoharness[redis]`.
- Publish compatibility, semantic-versioning, and deprecation rules for core
  protocols, serialized models, and adapters.

The memory boundary intentionally separates mechanism from policy:

```text
MemoryProvider: add, search, update, delete
MemoryPolicy:   recall, rank, inject, consolidate
```

Mem0 can implement the provider contract while applications remain free to
replace the recall and retention policy.

## Phase 4 — Observability and production readiness

Goal: make runs inspectable, measurable, and safe to operate at scale.

- Define a telemetry contract for run, step, model, and tool spans.
- Add OpenTelemetry and LangFuse adapters.
- Report token usage, latency, retries, tool failures, and estimated cost.
- Add distributed and multi-agent orchestration after session and checkpoint
  semantics are stable.
- Define explicit sandbox, secret, network, permission, and audit contracts
  instead of relying only on application conventions.
- Add deterministic trace replay and fault-injection tests for model, tool,
  state, and hook failures.
- Establish the cross-cutting [Agent Testing](AGENT_TESTING.md) track for
  scenarios, test oracles, behavioral coverage, mutation testing, differential
  execution, and failure reduction.
- Build an automated ETCSLV completeness matrix.
- Publish production guidance for permission policy, sandboxing, secrets, and
  prompt-injection boundaries.

## First ecosystem milestone: memory

The first end-to-end integration milestone will validate the adapter design:

1. Define vendor-neutral `Memory` and `MemoryProvider` models.
2. Separate recall and retention decisions into `MemoryPolicy`.
3. Provide an in-memory implementation for deterministic tests.
4. Implement a Mem0 adapter behind an optional dependency.
5. Run the same provider contract suite against both implementations.
6. Demonstrate memory recall and consolidation through lifecycle hooks without
   modifying `NanoEngine`.

## Adapter acceptance criteria

An official adapter is ready when it:

- exposes no vendor-specific objects through core interfaces;
- declares capabilities and unsupported operations explicitly;
- keeps dependencies optional;
- handles provider errors through normalized NanoHarness errors;
- passes the shared contract suite;
- includes a minimal example and security notes;
- documents the upstream versions against which it is tested.
