# AgentDojo Original-Scorer Bridge Pilot

This pilot validates that converted AgentDojo Scenarios can execute inside the
pinned native environment and be judged by the original user-task utility
scorer without treating Runtime-specific state as NanoHarness state. It is a
scorer-bridge integration check, not a model or mutation experiment.

## Frozen provenance

- Distribution: `agentdojo==0.1.35`
- Benchmark version: `v1.2.2`
- Source revision: `a75aba7631d3ca5fb7ab938965c97ead2f9ff84b`
- NanoHarness bridge revision:
  `b693aaa924ae1c0694f40fd9064982a633893206`
- Source conversion manifests:
  - Workspace: `f092448d01872a1de988cd06e45bdc3338a809b3f54a45e68073f9f1d1dbd4d4`
  - Banking: `848204a82685fb695a8bf05b83ce2d3e97844b112e77379d7908b5414785f1b7`
- Execution Manifest digest:
  `a7d5ad3025193c09ee8283774bbf04a9a01430495ac521a29d107f110d7d7b9f`

The four cells use the same development-only Workspace and Banking tasks as
the conversion pilot, one fixed seed each. The seed is recorded but controls
no stochastic source because `GroundTruthPipeline` invokes no model.

## Bound execution semantics

`AgentDojoSubjectAdapter.scenario_for()` derives a new execution Scenario from
the immutable unbound conversion record. The derived Scenario binds exactly
one `goal_achievement` Oracle to the original AgentDojo utility result. The
adapter then:

1. loads and initializes a fresh native environment;
2. rejects pre-environment digest drift;
3. creates a fresh pipeline and instrumented `FunctionsRuntime`;
4. preserves AgentDojo's maximum-three-attempt and final-attempt scorer-trace
   semantics;
5. calls `utility_from_traces`, falling back to strict `utility` exactly as the
   source suite does;
6. records pre/post digests, scorer path, native function trace, underlying
   tool attempts, delivered tool observations, and normalized model messages.

The archived GroundTruth run has four passed utility Verdicts and zero execution
errors. This is the expected reference outcome and validates plumbing only.

## Reproduction

From the repository root:

```bash
uv pip install --python .venv/bin/python -e '.[agentdojo-research]'
.venv/bin/python research/pilots/agentdojo_scorer_bridge/run.py \
  --output /tmp/agentdojo-scorer-report.json
```

The archived artifacts have these SHA-256 values:

```text
a857f6f13eab6b1dae3ff1dd3d9d5e96e4e48809a4de16309755cc135cf44bd1  manifest.json
6a2e9fe14941ae7cdd87a6e67f06db0428525469da612f6027897a05de2424ab  raw/experiment_report.json
```

Runtime timestamps and UUIDs make a new report byte-different. Reproduction
checks frozen identities, cells, scorer binding, task outcomes, environment
digests, tool/message evidence, and summary counts rather than raw-byte
equality.

## Claim boundary

`GroundTruthPipeline` deterministically executes each task author's reference
calls; it is not a realistic autonomous agent and invokes no live model. The
observed `4/4` is therefore not a task-success estimate, a comparison baseline,
evidence of prompt-injection resistance, or evidence that the converted tasks
generalize. No Mutant is applied, so it is not a Mutation Score. The selected
tasks are development examples, not held-out confirmatory cells. Paper evidence
still requires preregistered task sampling, real agent subjects, executable
faults that preserve sandbox semantics, and cluster-aware analysis.
