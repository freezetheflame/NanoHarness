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

## AI assistance disclosure record

Codex assisted with:

- repository inspection and implementation drafts;
- test-case generation and execution;
- official venue-page retrieval and comparison;
- provisional paper positioning, research questions, and experiment-plan text.

The human project owner selected and authorized the development direction and
must review the final research design, run and audit the experiments, validate
the related-work analysis, and approve all manuscript claims and citations.
