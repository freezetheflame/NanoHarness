# Agent Testing Strategy

[中文](AGENT_TESTING_CN.md) | English | [Roadmap](ROADMAP.md)

NanoHarness treats agent evaluation and agent testing as related but distinct
activities. Evaluation asks whether an agent achieved a task. Testing must also
make failures reproducible, check invariants throughout execution, measure what
behavior a suite exercises, and demonstrate that the suite detects plausible
defects.

The testing package remains outside the minimal runtime kernel. It consumes
stable NanoHarness events and models through the same component boundaries used
by applications and integrations.

## Testing model

| Capability | Question answered |
|---|---|
| `Evaluator` | Did the agent achieve the user's goal? |
| `TestOracle` | Did the run violate a state, policy, safety, or lifecycle invariant? |
| `TraceRecorder` | What inputs, decisions, actions, and state changes occurred? |
| `ReplayProvider` | Can the failure be reproduced without live models or side effects? |
| `CoverageCollector` | Which behaviors, rules, transitions, and failures were exercised? |
| `FaultInjector` | What happens when a dependency is slow, unavailable, or malformed? |
| `MutationRunner` | Can the test suite detect realistic defects deliberately inserted into a run? |
| `DifferentialRunner` | Which behavior changes across models, prompts, policies, or runtime versions? |
| `FailureReducer` | What is the smallest scenario and trace that still reproduce the failure? |

## Proposed package

```text
nanoharness/testing/
  scenario.py       # Scenario, fixture, expected invariants, and seed
  oracle.py         # deterministic, model-based, and LLM-assisted oracles
  trace.py          # canonical versioned events and trace serialization
  replay.py         # replay model, tools, time, retries, and policy decisions
  faults.py         # declarative fault schedules and injection hooks
  coverage.py       # behavioral coverage collection and reports
  mutation.py       # agent-specific mutation operators and mutation score
  differential.py   # controlled A/B execution and trace comparison
  shrink.py         # failing scenario and trajectory reduction
  pytest_plugin.py  # fixtures, assertions, markers, and CI output
  adapters/
    harbor.py
    inspect.py
    tau3.py
    agentdojo.py
```

## Canonical trace and replay

A trace records enough information to reproduce the control flow without
calling a live model or repeating an external side effect:

- messages sent to the model and the raw normalized response;
- tool schemas, calls, call IDs, results, errors, and durations;
- permission and policy decisions;
- context compression and state transitions;
- hook inputs, outputs, and failures;
- evaluator inputs and verdicts;
- time, retry, random-seed, budget, and stop-reason decisions.

Trace schemas are versioned and redact secrets before persistence. Replay must
declare whether an event is simulated, verified against a live dependency, or
unsupported. Calls with external side effects are never repeated implicitly.

## Test oracles

Task success is only one oracle. Scenarios may also define invariants such as:

- every tool call has exactly one terminal execution record;
- a denied call never reaches the underlying tool;
- a read-only task performs no write or external side effect;
- lifecycle start and end events are paired;
- a completed run has an evaluation verdict and explicit stop reason;
- checkpoint recovery does not repeat an already completed call ID;
- untrusted tool output cannot flow into a sensitive sink without approval.

Deterministic oracles are preferred. LLM-assisted oracles must expose their
prompt, model, repetitions, confidence, and disagreement rather than appearing
as deterministic assertions.

## Behavioral coverage

Agent test adequacy cannot be represented by source-line coverage alone. The
initial coverage model includes:

- tool and tool-argument partition coverage;
- run-status and stop-reason coverage;
- session and environment state-transition coverage;
- permission, policy-rule, and lifecycle-event coverage;
- injected-fault and recovery-path coverage;
- checkpoint and replay-path coverage;
- untrusted-source to sensitive-sink security coverage.

Coverage reports identify unexercised behavior; they do not claim that covered
behavior is correct. Thresholds are opt-in until the metrics are empirically
validated.

## Mutation testing

The mutation engine inserts plausible defects at component boundaries and
measures whether the test suite detects them. The initial operator set is based
on real NanoHarness failure modes:

```text
MODEL_RESPONSE_DROP       drop or truncate a model response
TOOL_NAME_SWAP            call a different registered tool
TOOL_ARGUMENT_DROP        remove a required argument
TOOL_RESULT_STALE         return a stale observation
TOOL_CALL_DUPLICATE       repeat a side-effecting call
CONTEXT_MESSAGE_DROP      lose the original user goal
HOOK_SKIP                 omit a lifecycle event
PERMISSION_BYPASS         execute after a denial
CHECKPOINT_CORRUPT        alter serialized session state
EVALUATOR_FLIP            change the achieved verdict
TERMINATED_AS_SUCCESS     treat model termination as task success
```

The primary metric is mutation score: detected valid mutants divided by all
valid mutants. Equivalent and invalid mutants are reported separately.

## Delivery sequence

1. **Implemented:** define versioned `TraceEvent` models and a `TraceRecorder`.
2. **Implemented:** add deterministic recording/replay adapters for LLM and
   tool boundaries, with strict request and cross-component order checking.
3. **Core implemented:** define serializable `Scenario`, deterministic
   `TestOracle`, and `ScenarioRunner` contracts. A dedicated pytest fixture and
   marker plugin remains to be implemented.
4. Add declarative fault injection for model, tool, state, and hook boundaries.
5. Publish the first behavioral coverage report.
6. Implement the initial mutation operators and mutation score.
7. Add differential execution and automatic failure reduction.
8. Integrate external environments and scorers through adapters.

Each step includes its own contract suite. Testing components must not require
changes to `NanoEngine` unless a missing runtime event or model is itself the
contract being introduced.

## Ecosystem adapters

- [Harbor](https://github.com/harbor-framework/harbor) for containerized,
  parallel benchmark environments and agent adapters.
- [Inspect AI](https://github.com/UKGovernmentBEIS/inspect_ai) for datasets,
  scorers, sandboxes, and evaluation composition.
- [tau3-bench](https://github.com/sierra-research/tau2-bench) for stateful,
  policy-constrained tool-agent-user scenarios.
- [AgentDojo](https://github.com/ethz-spylab/agentdojo) for prompt-injection
  attacks, defenses, and security scenarios.
- [Hypothesis](https://github.com/HypothesisWorks/hypothesis) for generation
  and shrinking of tool arguments and action sequences.

Adapters translate external objects into NanoHarness scenarios, traces, and
verdicts. External benchmark formats never become kernel interfaces.

## Research and publication plan

The central claim to test is that task-success evaluation alone is insufficient
for agent reliability engineering. The empirical study asks:

1. Does deterministic replay improve failure reproduction rate and diagnosis
   cost?
2. Does behavioral coverage reveal gaps that aggregate task success hides?
3. Does mutation score measure the defect-detection strength of agent tests?
4. Does fault injection expose recovery and lifecycle failures absent from
   normal benchmark runs?

Experiments start with deterministic NanoHarness scenarios and historical
Coding Agent defects, then extend to tau3-bench or AgentDojo. Report task
success, invariant violations, mutation score, recovery rate, flaky rate,
reproduction rate, diagnosis time, latency, and token cost. Compare a baseline
that uses only final evaluation with the full testing pipeline.

The working article theme is **Beyond Agent Evaluation: Bringing Software
Testing Principles into Agent Harnesses**.
