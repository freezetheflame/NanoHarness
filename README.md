<p align="center">
  <img src="assets/NanoharnessMain.png" alt="NanoHarness" width="640">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License: MIT">
  <img src="https://img.shields.io/badge/Tests-508%20passed-brightgreen.svg" alt="Tests">
  <img src="https://img.shields.io/badge/Framework-ETCSLV-purple.svg" alt="ETCSLV">
</p>

<h1 align="center">NanoHarness</h1>

<p align="center">
  <b>A minimal agent harness based on H&nbsp;=&nbsp;(E,&nbsp;T,&nbsp;C,&nbsp;S,&nbsp;L,&nbsp;V)</b>
</p>

English | [中文](README_CN.md)

---

## What

NanoHarness is a minimal Python framework for building tool-augmented LLM agents. It implements the six-component governance model from the [Agent Harness Survey](https://github.com/Gloriaameng/Awesome-Agent-Harness):

| | Component | Responsibility |
|:---:|---|---|
| **E** | Execution Loop | Think → Act → Observe cycle, termination, error recovery |
| **T** | Tool Registry | Typed tool catalog, routing, schema validation |
| **C** | Context Manager | Context window composition and compaction |
| **S** | State Store | Cross-turn persistence and crash recovery |
| **L** | Lifecycle Hooks | Cross-cutting instrumentation: logging, policy, auth |
| **V** | Evaluation | Trajectory recording, mid-loop early-stop detection, independent goal verification |

The kernel provides **only** these six interfaces and one orchestration engine. Everything else — which LLM to call, how to manage memory, whether to enforce permissions — is determined by the application.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                       NanoHarness Kernel                        │
│                                                                 │
│   ┌─────────────────────────────────────────────────────────┐  │
│   │  E: NanoEngine                                          │  │
│   │                                                         │  │
│   │    ON_START ──► Think ──► Act ──► Observe ──► ON_STEP   │  │
│   │                    │         │          │       │        │  │
│   │                    ▼         ▼          ▼       ▼        │  │
│   │               LLMProtocol  T: Tools  C: Context         │  │
│   │                                              V: Eval    │  │
│   │                                    should_stop? ──► STOP │  │
│   │                                                         │  │
│   │    ON_END ◄── V: Report + evaluate_success              │  │
│   └─────────────────────────────────────────────────────────┘  │
│                                                                 │
│   Interfaces:  BaseToolRegistry  BaseContextManager             │
│                BaseStateStore    BaseHookManager                │
│                BaseEvaluator     LLMProtocol                    │
└─────────────────────────────────────────────────────────────────┘
                              │
                   constructor injection
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Application Layer                          │
│                                                                 │
│   LLM adapter  ·  memory strategy  ·  permission policy        │
│   tool assembly  ·  prompt templates  ·  UI / output           │
│                                                                 │
│   Wiring: main.py or per-project builder                        │
└─────────────────────────────────────────────────────────────────┘
```

**Design principle:** The engine has zero knowledge of prompts, memory, permissions, or I/O. All behavior is injected. This makes the kernel safe to share across different agent applications.

---

## Structure

```
nanoharness/
  core/                  # Kernel: interfaces + engine
    schema.py            #   Interaction, evaluation, and canonical RunResult models
    base.py              #   ETCSLV ABCs, LLMProtocol, HookStage
    engine.py            #   NanoEngine (with mid-loop evaluation)
    prompt.py            #   PromptManager (YAML template loader)
  components/            # Minimal ETCSLV implementations
    tools/               #   T: DictToolRegistry, ScriptToolRegistry
    context/             #   C: SimpleContextManager
    state/               #   S: JsonStateStore
    hooks/               #   L: SimpleHookManager
    evaluator/           #   V: TraceEvaluator (with should_stop + evaluate_success)
  testing/               # Trace recording and future replay/testing components
  utils/                 # get_logger, count_tokens
configs/
  prompts.yaml           # Prompt templates
  scripts/               # Shell-script tools (auto-discovered, 27 tools)
examples/
  coding_agent/          # Full-featured coding agent reference (434 tests)
tests/                   # Kernel contract tests
```

---

## Quick Start

```bash
git clone https://github.com/HabitGraylight/NanoHarness.git
cd NanoHarness
pip install -e .
```

The kernel depends only on Pydantic and PyYAML. LLM clients and other integrations are installed by each application as needed.

```bash
# Run the minimal example
python main.py

# Run the coding agent
cd examples/coding_agent && python main.py
```

---

## Engine Loop

```
NanoEngine.run(query)
     │
     ├─ L.trigger(ON_TASK_START)
     ├─ C.add_message(user)
     │
     └─ loop until terminated or max_steps:
          │
          ├─ Think:  E → LLM.chat(C.get_full_context(), T.get_schemas())
          ├─ L.trigger(ON_THOUGHT_READY)
          │
          ├─ Act:    for each tool_call:
          │            optional permission gate → T.call(name, args)
          │            C.add_message(observation)
          │
          ├─ S.save_state()
          ├─ V.log_step()
          ├─ V.should_stop()?  ──► early break if stuck/spinning
          └─ L.trigger(ON_STEP_END)

     ├─ V.get_report()        (includes evaluate_success verdict)
     ├─ E builds RunResult    (status + stop reason + final answer + trajectory)
     └─ L.trigger(ON_TASK_END)
```

No memory, no prompt rendering, no permission logic inside the engine. All of that flows through injected components and hooks.

`RunResult.status` describes how execution ended (`completed`, `stopped`, or
`exhausted`), while `RunResult.evaluation.achieved` is the sole success
verdict. Stopping is therefore not automatically treated as completing the
user's goal. Legacy `report["summary"]` and `report["trajectory"]` access is
kept during migration.

Lifecycle traces can be collected without changing the engine:

```python
from nanoharness.testing import TraceRecorder

recorder = TraceRecorder()
recorder.attach(hooks)
result = engine.run("Complete the task")
trace_json = recorder.snapshot().model_dump_json(indent=2)
```

Trace payloads are normalized into versioned NanoHarness models. Common
secret-bearing fields are redacted by default; applications can inject a
stricter redactor for secrets embedded in free-form text.

Model and tool boundaries can be recorded and replayed deterministically:

```python
from nanoharness.testing import (
    RecordingLLM,
    RecordingToolRegistry,
    ReplayLLM,
    ReplaySession,
    ReplayToolRegistry,
)

# Record a live run.
recording_llm = RecordingLLM(live_llm, recorder)
recording_tools = RecordingToolRegistry(live_tools, recorder)

# Replay it without calling either live dependency.
session = ReplaySession(recorder.snapshot())
replay_llm = ReplayLLM(session)
replay_tools = ReplayToolRegistry(session)
```

Sharing one `ReplaySession` checks the global model/tool interaction order.
Strict mode also checks messages, schemas, tool names, and arguments. Recorded
dependency failures are reproduced as structured `RecordedExecutionError`s.

Serializable scenarios compose deterministic oracles without adding policy to
the engine:

```python
from nanoharness.testing import OracleKind, OracleSpec, Scenario, ScenarioRunner

scenario = Scenario(
    scenario_id="completes-once",
    query="Complete the task",
    oracles=[
        OracleSpec(kind=OracleKind.GOAL_ACHIEVEMENT, parameters={"expected": True}),
        OracleSpec(kind=OracleKind.LIFECYCLE),
    ],
)
report = ScenarioRunner(engine_factory).run(scenario)
report.raise_for_failure()
```

Built-in oracles cover goal achievement, run status, stop reason, lifecycle
pairing, tool-call constraints, component failures, and expected execution
errors. Oracle configuration is validated before an engine or live dependency
is invoked.

Trace-level mutation campaigns can measure whether those oracles detect
controlled observable faults:

```python
from nanoharness.testing import (
    ContextMessageDropOperator,
    HookSkipOperator,
    MutationRunner,
)

campaign = MutationRunner(scenario_runner).run(
    scenario,
    [HookSkipOperator(), ContextMessageDropOperator()],
)
print(campaign.mutation_score)
```

The score denominator contains only killed and survived mutants. Baseline
failures, non-applicable, equivalent, invalid, and erroneous mutants are
explicit outcomes and are excluded rather than silently counted as survived
mutants.

For faults that must affect real control flow, `FaultPlan` rules can be applied
at live or deterministic-fixture model, tool, Context, state, hook, and
permission boundaries. `FaultCampaignRunner`
runs a clean baseline plus a fresh engine for every plan, records exactly which
rules triggered and whether they changed a value, then classifies the execution
through the same Scenario Oracles. Place model/tool result-transforming faults
inside their recording decorators. Place Context/state/hook/permission
suppression faults outside recording so skipped operations are not falsely
recorded as completed.
Corresponding recording decorators expose normalized Context, state, hook, and
permission events. Trace schema v2 carries these events; deterministic replay
migrates legacy v1 model/tool traces without mutating their source objects.

Real-defect evidence is kept in a versioned `DefectCorpus` rather than informal
notes. The [corpus protocol](REAL_DEFECT_PROTOCOL.md) separates operator
derivation from held-out validation, retains independent coder annotations, and
defines evidence and freeze gates. Inspect the current draft corpus with:

```bash
.venv/bin/python -m nanoharness.testing.defect_cli \
  research/defects/corpus.json
```

Candidate records are not counted as verified defects.

Behavioral coverage uses a preregistered `CoverageModel`; its targets—not the
behaviors already observed—define the denominator. `CoverageCollector` supports
tool and argument classes, run/stop outcomes, lifecycle and component failures,
Oracle outcomes, executable faults, and Trace metadata. See the
[coverage protocol](BEHAVIORAL_COVERAGE.md) and the explicitly non-frozen
template at `research/coverage/model_template.json`. No coverage threshold is
enabled by default.

External subjects implement a small `SubjectAdapter` contract. Frozen
`ExperimentManifest` files bind subject revisions, complete Scenarios, cell
order, seeds, and metadata to a digest before execution. The pinned real
LangGraph plumbing pilot can be reproduced with:

```bash
uv pip install --python .venv/bin/python -e '.[research]'
.venv/bin/python research/pilots/langgraph_deterministic/run.py \
  --output /tmp/langgraph-pilot-report.json
```

See the [experiment protocol](EXPERIMENT_PROTOCOL.md) and Pilot README for its
strict claim boundary; the echo Pilot is not paper-scale evidence.

The companion `research/pilots/langgraph_tool_faults/` Pilot runs three
executable Tool mutants inside a real StateGraph. Its attempt-aware Trace keeps
duplicate underlying executions distinct from the single Observation delivered
to graph state. All three are killed in the archived plumbing run, but the
handcrafted sample is not a paper Mutation Score.

External Benchmark ingestion is kept separate from subject execution. The
pinned AgentDojo converter freezes package/commit provenance, explicit task
IDs, environment digests, reference calls, and original scorer provenance:

```bash
uv pip install --python .venv/bin/python -e '.[agentdojo-research]'
.venv/bin/python research/pilots/agentdojo_offline_conversion/run.py \
  --output-dir /tmp/agentdojo-conversion
```

Converted reference calls are non-normative and their Oracles remain unbound;
see the Pilot README before using these Scenarios in an experiment.

The companion `agentdojo_scorer_bridge/` Pilot derives explicitly bound
execution Scenarios and invokes the original utility over native AgentDojo
pre/post environments. Its deterministic GroundTruthPipeline result validates
the bridge only; it is not an agent-performance result.

---

## Tools

Tools satisfy `BaseToolRegistry` with two methods: `get_tool_schemas()` and `call(name, args)`.

Two built-in registries:

- **DictToolRegistry** — register Python functions via `@tool` decorator. JSON Schema is inferred from type hints.
- **ScriptToolRegistry** — auto-discovers `.sh` files in a directory. Parameters are declared via `@param` comment headers and passed as environment variables.

Registries compose via `merge()`.

Adding a new tool does not require touching any Python code — drop a shell script with the right headers into `configs/scripts/` and it is automatically available to the agent.

---

## Extending

The kernel defines interfaces. Applications provide concrete behavior:

**LLM** — implement `LLMProtocol`:
```python
def chat(self, messages, tools=None) -> LLMResponse: ...
```

**Custom components** — subclass any `Base*` ABC and inject into `NanoEngine`.

See `examples/coding_agent/` for a reference that wires together a custom LLM adapter, memory strategy, permission pipeline, subagent delegation, skill loading, and evaluation — all built on top of the kernel without modifying it.

---

## Testing

```bash
# Kernel tests (74)
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -v

# Coding agent tests (434: 291 UT + 143 ST)
cd examples/coding_agent
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -v
```

**Total: 508 tests.** Kernel tests require only the kernel dependencies and pytest.

---

## Roadmap

NanoHarness follows a problem-driven, component-oriented roadmap. The kernel
defines stable contracts; optional adapters connect those contracts to the
open-source ecosystem without leaking vendor-specific objects into the engine.

- Phase 1: correctness and component contracts
- Phase 2: execution, sessions, and recovery
- Phase 3: memory and ecosystem adapters
- Phase 4: observability and production readiness

See the [full roadmap](ROADMAP.md) or the [Chinese version](ROADMAP_CN.md).

---

## Security

Agents with tool access can cause real damage. Production deployments should implement permission gates, sandbox execution, and prompt injection defenses. See the coding agent example for a reference permission pipeline.

---

## Acknowledgments

The theoretical foundation of this project is based on the [Agent Harness Survey](https://github.com/Gloriaameng/Awesome-Agent-Harness).

---

## Citation

```bibtex
@software{nanoharness2026,
  title     = {NanoHarness: A Minimal Agent Harness Based on H=(E,T,C,S,L,V)},
  author    = {Habit},
  year      = {2026},
  url       = {https://github.com/HabitGraylight/NanoHarness},
  license   = {MIT}
}
```

Theoretical foundation:

```bibtex
@article{meng2026agentharness,
  title     = {Agent Harness for Large Language Model Agents: A Survey},
  author    = {Meng, Qianyu and Wang, Yanan and Chen, Liyi and others},
  year      = {2026},
  url       = {https://www.preprints.org/manuscript/202604.0428/v2}
}
```

---

## License

MIT
