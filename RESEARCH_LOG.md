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

## AI assistance disclosure record

Codex assisted with:

- repository inspection and implementation drafts;
- test-case generation and execution;
- official venue-page retrieval and comparison;
- provisional paper positioning, research questions, and experiment-plan text.

The human project owner selected and authorized the development direction and
must review the final research design, run and audit the experiments, validate
the related-work analysis, and approve all manuscript claims and citations.
