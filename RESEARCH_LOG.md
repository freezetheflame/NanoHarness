# Research Log

This log records decisions, evidence, artifact changes, and AI assistance that
may affect the claims or reproducibility of the planned agent-testing paper.
Entries describe work performed; they do not by themselves validate a research
claim. Human authors must verify all claims, citations, data, and analyses.

## 2026-07-30 — Venue and positioning scan

- Inspected the official calls and dates for ICST 2027, CAIN 2027, FSE 2027,
  and ICSE 2027.
- Current recommendation: ICST 2027 Research Papers, positioned around
  agent-specific mutation testing and test adequacy.
- Strong alternative: CAIN 2027, positioned around a broader testability
  architecture for agentic software.
- Decision remains pending human confirmation.
- Identified a key novelty risk: ISSTA 2026 already includes work on agent
  behavioral diagnosis and security testing, including AgentInspect and
  AgentBreaker. The planned work must establish a clear test-suite-adequacy
  contribution rather than claim novelty from trajectory diagnosis alone.

Official sources consulted:

- https://conf.researchr.org/track/icst-2027/icst-2027-research-papers
- https://conf.researchr.org/track/cain-2027/cain-2027-call-for-papers
- https://conf.researchr.org/track/fse-2027/fse-2027-papers
- https://conf.researchr.org/track/icse-2027/icse-2027-research-track
- https://conf.researchr.org/track/issta-2026/issta-2026-research-papers

## 2026-07-30 — Trace recording foundation

- Added versioned `AgentTrace` and `TraceEvent` models.
- Added a thread-safe lifecycle `TraceRecorder` with JSON serialization and
  configurable redaction.
- Verified the implementation with 89 core tests at commit `8da896c`.
- This establishes event collection but does not yet prove replay correctness,
  mutation validity, or external generality.

## 2026-07-30 — Deterministic I/O replay foundation

- Added `RecordingLLM` and `RecordingToolRegistry` boundary decorators.
- Added a shared ordered `ReplaySession`, `ReplayLLM`, and
  `ReplayToolRegistry`.
- Strict replay checks model messages, tool schemas, tool names, arguments, and
  global model/tool interaction order.
- Recorded dependency failures are reproduced without calling live
  dependencies.
- Added rejection of unsupported trace versions, duplicate event IDs,
  non-monotonic sequences, and cross-trace event contamination.
- Current verification: 103 core tests and 36 targeted Coding Agent regression
  tests pass. Formatting diff checks pass. Ruff was attempted but its package
  download stalled, so no Ruff result is claimed.

## 2026-07-30 — Serializable scenarios and deterministic oracles

- Added versioned `Scenario`, `OracleSpec`, `OracleVerdict`, and
  `ScenarioReport` models.
- Added deterministic built-in oracles for goal achievement, run/stop outcome,
  lifecycle pairing, tool-call constraints, component errors, and expected
  execution errors.
- Added `ScenarioRunner`, which validates oracle configuration before execution,
  creates a fresh engine through an injected factory, records lifecycle events,
  and normalizes execution failures.
- Added custom-oracle registration and warning-severity semantics.
- Current verification after this increment: 117 core tests pass. The dedicated
  pytest plugin named in the strategy is not yet implemented.

## 2026-07-30 — Trace-level mutation testing foundation

- Added model-message, tool-argument, and tool-result deterministic oracles.
- Added seven initial trace/report mutation operators covering evaluator,
  lifecycle, context, tool arguments, tool results, and duplicate calls.
- Added `MutationRunner` and explicit killed, survived, not-applicable,
  equivalent, invalid, error, and baseline-failed outcomes.
- Mutation score excludes non-applicable, equivalent, invalid, and erroneous
  mutants; its denominator is killed plus survived mutants.
- Current verification after this increment: 127 core tests and 36 targeted
  Coding Agent regression tests pass.
- Scope limitation: these operators mutate detached observations and reevaluate
  oracles. They do not yet execute arbitrary control-flow-changing agent
  mutants. The paper and experiments must keep that distinction explicit.

## 2026-07-30 — Executable model/tool fault injection foundation

- Added versioned, serializable `FaultPlan` and `FaultRule` contracts with
  deterministic zero-based occurrence matching and validation of action/boundary
  compatibility.
- Added shared, thread-safe `FaultSession` evidence with ordered applications,
  effective/ineffective distinctions, and explicit untriggered rules.
- Added model and tool decorators for injected errors, dropped model responses,
  swapped tool names, dropped tool arguments, stale tool results, and duplicate
  tool execution.
- Added `FaultCampaignRunner`, which runs a clean baseline and a fresh engine
  per plan, records fault provenance in Trace metadata, and classifies executable
  outcomes through the same deterministic Scenario Oracles.
- Current verification after this increment: 145 core tests and 36 targeted
  Coding Agent regression tests pass.
- Scope limitation: this layer supports model/tool boundaries with deterministic
  fixtures or sandboxed live dependencies. Context, state, permission, hook,
  and branch-aware replay injection remain future work.

## 2026-07-30 — Real-defect corpus protocol foundation

- Added a bilingual protocol with explicit sampling, inclusion/exclusion,
  evidence, deduplication, independent coding, adjudication, and reproducibility
  requirements.
- Separated operator-derivation defects from held-out validation defects to
  prevent circular RQ1 evidence.
- Added versioned `DefectCorpus`, `DefectRecord`, evidence, annotation, and
  partition models with validation for verified and frozen records.
- Added deterministic summaries, Cohen's kappa for inclusion decisions, Jaccard
  agreement for multi-label coding, validation mapping rate, and preregistered
  readiness gates.
- Added a JSON analysis CLI and a machine-readable seed corpus. The single seed
  record is explicitly `candidate`; it is not claimed or counted as a verified
  real defect before independent human coding and adjudication.
- Current verification after this increment: 162 core tests and 36 targeted
  Coding Agent regression tests pass.

## 2026-07-30 — Explicit-universe behavioral coverage foundation

- Added versioned `CoverageModel` and `CoverageTarget` contracts whose declared
  targets, rather than observed behavior, define the denominator.
- Added deterministic tool-argument equivalence classes and targets for run,
  stop, lifecycle, errors, Oracle outcomes, fault applications/outcomes, and
  Trace metadata.
- Added per-hit redacted evidence, cross-run set-union aggregation,
  per-dimension summaries, uncovered required targets, and opt-in gates with no
  default threshold.
- Frozen models bind a subject revision and derivation to a SHA-256 target digest;
  experiment mode rejects templates, drafts, and post-freeze mutation.
- Added a bilingual measurement protocol and an explicitly non-frozen generic
  template. Subject-specific target derivation and empirical validation remain
  required before the metric supports a paper claim.
- Current verification after this increment: 181 core tests and 36 targeted
  Coding Agent regression tests pass.

## 2026-07-30 — Runtime-boundary recording and executable faults

- Added normalized Context message/snapshot, state save/load, hook-stage, and
  permission-decision recording decorators, including boundary error events.
- Added executable Context-message drop, checkpoint corruption, state-save
  drop, hook skip, and permission-bypass actions to the shared deterministic
  `FaultSession` model.
- Added deterministic state-value and permission-enforcement Oracles. The latter
  detects a tool execution whose nearest preceding decision for that tool was a
  denial.
- Extended behavioral coverage to Context roles, state value classes, hook
  outcomes, permission decisions, and the new fault actions.
- Bumped Trace, Fault, and Coverage schemas for the expanded vocabularies.
  Trace and Fault v1 inputs have explicit detached upgrades; Coverage v1 models
  remain readable. Unsupported or internally inconsistent versions are rejected.
- Executable Campaign tests demonstrate Oracle kills for live Context drop,
  hook skip, checkpoint corruption, and permission bypass. Branch-aware replay
  after a control-flow divergence remains unimplemented.
- Current verification after this increment: 207 core tests and 36 targeted
  Coding Agent regression tests pass.

## 2026-07-30 — External subject and frozen experiment pilot

- Added immutable `SubjectIdentity`, generic callable and ScenarioRunner
  adapters, digest-bound experiment manifests, serial seeded execution, raw
  observation reports, and strict Adapter/Trace provenance checks.
- Added an optional LangGraph Adapter that creates a fresh graph per observation
  and streams full state values instead of reconstructing reducer semantics from
  partial updates.
- Pinned the research dependency to `langgraph==1.2.10` and ran a real compiled
  StateGraph integration test.
- Froze Pilot Manifest
  `38172c288e5d95a0a374713d768a5ec9517a798dc134a7b8a6afe7bb75f23b8a`
  against Harness revision `10a5cb24e97e0a032f8ec289e919da60d811d0be`.
- The archived Pilot contains two deterministic echo Scenarios, three seeds
  each, six passed observations, zero execution errors, and raw-report SHA-256
  `01e44e7a621bf95a7798a72fc59f2e56d7ce99e2a3c36b143cd74e905ece4094`.
- Claim limitation: this Pilot validates the external Adapter and replication
  pipeline only. It has no live model, tool side effects, mutants, paper-scale
  scenarios, or external Benchmark dataset and cannot support the paper's main
  empirical claims.
- Current verification after this increment: 220 core tests (including the
  installed real LangGraph integration and archived Artifact audit) and 36
  targeted Coding Agent regression tests pass.

## 2026-07-30 — External executable LangGraph Tool-Fault Pilot

- Added Recorder-aware LangGraph builders and a digest-bound external
  `SubjectFaultCampaignRunner` that creates a fresh Adapter for baseline and
  every Fault Plan.
- Corrected Tool fault instrumentation so underlying attempts use
  `TOOL_STARTED`/`TOOL_COMPLETED`/`TOOL_ERROR`, while one final `TOOL_EXCHANGE`
  records the Observation delivered to graph state and cumulative
  `attempt_count`.
- Froze the three-Plan Manifest
  `b9269b84e0f29400de90b6aa2b29ff2aea92ee6c9ded9b0dd048d8d1e1abe01e`
  against Harness/subject revision
  `457922a4beccf9372fb5daf39068389ad78395ed`.
- The real `langgraph==1.2.10` Pilot baseline passed. Stale result, duplicate
  call, and required-argument drop were all killed; the plumbing Mutation Score
  is `3/3=1.0`. Raw Campaign SHA-256 is
  `854add94ab1d9f2e7bcd765f72f94d4b58d63e2f9f3ddb1b2c524b2f740798c1`.
- Claim limitation: the three hand-selected Mutants and one deterministic local
  tool are not held-out or representative. This result validates integration
  and trace semantics only, not the paper's mutation-effectiveness claim.
- Current verification after this increment: 226 core tests (including both
  installed LangGraph integrations and archived Artifact audits) and 36 targeted
  Coding Agent regression tests pass.

## 2026-07-30 — Pinned AgentDojo Benchmark conversion pilot

- Added a Benchmark-specific layer separate from Runtime `SubjectAdapter`s,
  with immutable source provenance, explicit task selection, digest-bound
  Manifests, pre-environment digests, reference calls, and scorer provenance.
- Pinned `agentdojo==0.1.35`, Benchmark `v1.2.2`, and upstream Git revision
  `a75aba7631d3ca5fb7ab938965c97ead2f9ff84b`.
- Converted four real development tasks from Workspace and Banking. Frozen
  Manifest digests are
  `f092448d01872a1de988cd06e45bdc3338a809b3f54a45e68073f9f1d1dbd4d4`
  and `848204a82685fb695a8bf05b83ce2d3e97844b112e77379d7908b5414785f1b7`.
- The converter labels ground-truth calls as non-normative reference plans and
  leaves Scenario Oracles explicitly unbound because AgentDojo utility scorers
  require their native pre/post environments. This prevents conversion from
  silently changing success semantics.
- Claim limitation: the Pilot invokes no live model, executes no AgentDojo
  pipeline, computes no utility/security or mutation score, and is not a
  reproduction of AgentDojo results. It validates external data ingestion and
  artifact integrity only; scorer-bound execution and held-out paper-scale
  selection remain required.
- Current verification after this increment: 230 non-AgentDojo tests and 2
  real-package AgentDojo integration/artifact tests pass.

## AI assistance disclosure record

Codex assisted with:

- repository inspection and implementation drafts;
- test-case generation and execution;
- official venue-page retrieval and comparison;
- provisional paper positioning, research questions, and experiment-plan text.

The human project owner selected and authorized the development direction and
must review the final research design, run and audit the experiments, validate
the related-work analysis, and approve all manuscript claims and citations.
