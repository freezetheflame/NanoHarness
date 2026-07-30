# Agent-Testing Experiment and Replication Protocol

English | [中文](EXPERIMENT_PROTOCOL_CN.md) | [Paper plan](PAPER_PLAN.md)

Status: **draft, venue decision pending**. This protocol defines execution and
data-integrity rules; hypotheses, final subjects, sample-size rationale, and
analysis thresholds must be frozen before the confirmatory runs.

## Frozen execution unit

An `ExperimentManifest` binds the following into a SHA-256 digest:

- immutable subject identity, Runtime version, Revision, and source;
- complete serializable Scenario and deterministic Oracles;
- cell order and every repetition seed;
- experiment ID and preregistered metadata.

The Runner rejects changed manifests, missing/extra adapters, identity drift,
duplicate Trace IDs, Scenario/seed provenance mismatch, naive or reversed wall
times, and non-monotonic duration clocks. It runs cells serially in manifest
order. Parallel execution may be added only with an explicit deterministic
scheduler and isolated environments.

A seed is execution metadata, not proof of determinism. Repeated identical
seeds are allowed to measure residual nondeterminism; distinct seeds measure
configured variation. Every Adapter must state which stochastic sources the
seed actually controls.

## Subject and Adapter requirements

Each subject uses a fresh Runtime/graph/engine per observation unless the
manifest explicitly studies persistent state. An independently developed
subject requires a stable source URL and immutable package/commit Revision.
Adapters normalize lifecycle and subject provenance without claiming that
Runtime-specific states are semantically identical.

External Adapter contract tests with fakes establish interface behavior only.
At least one pinned real dependency must execute in CI or a separately recorded
integration job. The current LangGraph pilot satisfies this plumbing check but
is deliberately too small to count as paper-scale external validation.

## Experimental conditions

For each eligible subject/scenario pair, preserve raw results for these
conditions when supported:

1. final task-success evaluation only;
2. deterministic invariant Oracles without mutation;
3. trace-level mutation with no new external calls;
4. executable boundary faults using fresh deterministic fixtures or sandboxes;
5. live execution without Replay;
6. deterministic Replay of the same recorded boundary interactions.

The same frozen Scenario and relevant Mutant identity must be paired across
conditions. Unsupported conditions are explicit missing cells, not zeros.
Baseline failure, invalid, equivalent, not-applicable, and infrastructure error
outcomes remain outside the killed/survived denominator and are reported.

## Raw observations and artifacts

Never overwrite the archived confirmatory run. Store:

- frozen manifests and digests;
- full Scenario, Trace, RunResult, Verdict, Fault/Mutation evidence;
- subject and Harness revisions;
- repetition index and seed;
- latency, model/tool calls, token/cost inputs when available;
- environment and dependency lock information;
- command, stdout/stderr, failure status, and artifact SHA-256.

Runtime noise such as timestamps, durations, and UUIDs means reruns need not be
byte-identical. Reproduction compares preregistered semantic fields and reports
their equality separately from timing variation. Redaction occurs before public
serialization; private source-to-redacted mappings are access-controlled.

## Primary estimands

- **RQ1:** held-out validation-defect mapping rate to the frozen Operator
  taxonomy, overall and by component boundary.
- **RQ2:** Mutation Score and per-Operator kill rate for final Evaluator alone
  versus the full deterministic Oracle suite.
- **RQ3:** association and discordance among task success, frozen behavioral
  coverage, and Mutation Score.
- **RQ4:** paired differences in reproduced Verdict, live calls, latency, token
  use, and estimated cost between live reruns and Replay.

The unit of inference is not an individual repeated run or Mutant when several
belong to one Scenario. Preserve the Subject/Scenario/Operator clustering and
use paired or cluster-aware intervals. Report effect sizes and confidence
intervals. Choose any null-hypothesis test from the preregistered data structure,
not after seeing which test is significant. Report missingness and failures.

## Pilot versus confirmatory evidence

Pilots may debug adapters, estimate runtime/cost/variance, and refine operational
procedures. Pilot observations cannot enter confirmatory estimates or motivate
post-hoc target/operator changes without a new frozen version. All such changes
remain in the Research Log.

Before confirmatory execution, freeze:

- venue framing, RQs, hypotheses, subjects, and exclusion criteria;
- real-defect corpus and Operator taxonomy;
- subject-specific Coverage Models;
- Scenario cells, seeds/repetitions, conditions, and sample-size rationale;
- primary estimands, aggregation units, missing-data handling, and stop rules.
