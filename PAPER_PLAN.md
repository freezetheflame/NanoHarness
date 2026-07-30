# Research Paper Plan: Testing Tool-Using AI Agents

[中文](PAPER_PLAN_CN.md) | English | [Agent Testing Strategy](AGENT_TESTING.md)

Status: **venue decision pending**. The current recommendation is the ICST
2027 Research Papers track. This document separates claims already supported
by the artifact from planned claims that still require implementation and
experiments.

## Venue decision

| Venue | Official deadline (AoE) | Best paper framing | Current assessment |
|---|---:|---|---|
| [ICST 2027 Research Papers](https://conf.researchr.org/track/icst-2027/icst-2027-research-papers) | 2 Nov 2026 | Agent-specific mutation testing and test adequacy | Recommended |
| [CAIN 2027 Research Track](https://conf.researchr.org/track/cain-2027/cain-2027-call-for-papers) | 30 Oct 2026 | Testability architecture for agentic software | Strong alternative |
| [FSE 2027 Research Papers](https://conf.researchr.org/track/fse-2027/fse-2027-papers) | 2 Oct 2026 | Broad, multi-runtime empirical SE contribution | High risk on current schedule |
| [ICSE 2027 Research Track](https://conf.researchr.org/track/icse-2027/icse-2027-research-track) | 30 Jun 2026 | Testing and analysis of AI-enabled systems | Closed |

ICST and CAIN have overlapping review periods and deadlines only three days
apart, so the same manuscript cannot use one as an immediate fallback for the
other. ISSTA remains a natural longer-horizon venue, but its 2027 call is not
public yet and its 2026 program already contains several agent testing and
diagnosis papers.

## Recommended ICST positioning

Working title:

> Do Your Agent Tests Actually Detect Faults? Mutation Testing for Tool-Using
> AI Agents

Central claim:

> Task-success evaluation does not measure whether an agent test suite can
> detect realistic faults in model, tool, context, lifecycle, and policy
> boundaries. Agent-specific mutation operators, deterministic replay, and
> behavioral adequacy metrics make this defect-detection ability measurable.

This framing deliberately treats NanoHarness as the experimental artifact, not
the paper's primary novelty.

## Novelty boundary

Existing agent benchmarks mainly measure task success. AgentDojo specializes in
prompt-injection attacks, while the ISSTA 2026 AgentInspect work diagnoses and
classifies behavioral failures from agent trajectories. The proposed work is
different in three ways:

1. it evaluates the **test suite**, rather than only the agent;
2. it inserts controlled, agent-specific faults before deployment;
3. it uses deterministic replay to separate defect detection from model
   sampling variance and repeated external side effects.

Before submission, this boundary must be validated through a systematic related
work review. It is a hypothesis, not yet a proven novelty claim.

## Planned contributions

1. A taxonomy of mutation operators derived from real agent-system defects and
   component boundaries.
2. A runtime-independent mutation model over normalized model, tool, context,
   lifecycle, evaluator, and policy events.
3. A deterministic recording/replay substrate for reproducible and side-effect
   safe mutation experiments.
4. Agent test-adequacy metrics, centered on mutation score and supported by
   behavioral coverage.
5. An empirical study comparing final-task evaluation with the proposed testing
   pipeline across multiple agent systems and scenarios.

## Research questions

- **RQ1 — Representativeness:** How well do the proposed mutation operators
  represent real defects observed in agent systems?
- **RQ2 — Detection:** How many valid agent mutants are detected by existing
  task-success evaluators and test suites?
- **RQ3 — Adequacy:** What is the relationship among mutation score, task
  success, and behavioral coverage?
- **RQ4 — Reproducibility and cost:** How much do deterministic replay and
  side-effect suppression reduce flaky outcomes, model calls, cost, and time?

## Artifact architecture

```text
Scenario + Oracle
       |
MutationRunner ---- MutationOperator
       |
RecordingLLM / RecordingToolRegistry
       |
Versioned AgentTrace
       |
ReplaySession
  |             |
ReplayLLM   ReplayToolRegistry
       |
CoverageCollector + MutationReport
```

Implemented evidence:

- canonical `RunResult`, `RunStatus`, and `StopReason`;
- versioned `AgentTrace` and `TraceEvent`;
- lifecycle `TraceRecorder` with redaction and thread-safe ordering;
- strict recording/replay adapters for model and tool boundaries;
- replay of recorded dependency failures without live side effects.
- serializable scenarios, prevalidated deterministic oracles, and fresh-engine
  scenario execution reports.

Still required:

- the dedicated pytest fixture/marker plugin;
- the real-defect corpus and mutation taxonomy protocol;
- at least six implemented mutation operators;
- `MutationRunner`, validity classification, and mutation score;
- behavioral coverage and experiment reporting;
- adapters or subjects beyond NanoHarness itself.

## Empirical design

### Subjects

The minimum credible study should include:

1. NanoHarness deterministic kernel scenarios;
2. the repository's full Coding Agent application;
3. at least one independently developed agent runtime or benchmark adapter.

Candidate external environments are tau3-bench and AgentDojo. A separate
runtime such as Google ADK or LangGraph would strengthen external validity more
than adding only another scenario set.

### Defects and mutants

- collect and label real defects from NanoHarness and public agent-framework
  issue/commit histories;
- derive mutation operators using a documented coding protocol;
- retain a mapping from every operator to motivating real defects;
- distinguish killed, survived, invalid, and equivalent mutants;
- review a stratified sample with at least two annotators and report agreement.

### Baselines

- final task-success evaluation only;
- deterministic invariant oracles without mutation;
- behavioral coverage without mutation score;
- replay disabled, requiring fresh model/tool execution.

### Metrics

- mutation score and per-operator kill rate;
- task success and invariant-violation rate;
- behavioral coverage;
- flaky verdict and reproduction rate;
- live model/tool calls avoided by replay;
- latency, tokens, and estimated monetary cost;
- invalid/equivalent mutant rate.

Report confidence intervals and effect sizes. Select statistical tests only
after inspecting the paired/repeated-measures structure; do not choose tests
merely because they produce significance.

## Submission gates

By the experiment freeze, the paper should have:

- a frozen operator taxonomy supported by a real-defect corpus;
- at least six working operators over four or more component boundaries;
- at least two agent systems plus one external scenario/benchmark source;
- a completely scripted, seeded, and replayable experiment pipeline;
- an anonymizable replication package with raw results and analysis scripts;
- evidence that the mutation score adds information beyond task success alone.

If these gates are not met by mid-September 2026, the safer paper should be
reframed for CAIN around testability architecture and preliminary evidence,
rather than making an under-supported ICST mutation-testing claim.

## Working schedule

- **By 15 Aug:** replay substrate, Scenario, and deterministic Oracle.
- **By 5 Sep:** real-defect protocol, initial corpus, six mutation operators,
  and MutationRunner.
- **By 20 Sep:** coverage metrics, external adapter, and pilot experiment.
- **By 10 Oct:** complete experiment runs and freeze raw data.
- **By 24 Oct:** complete paper draft and replication package.
- **25 Oct–1 Nov:** internal review, threat analysis, and final checks.

## Authorship and AI-use record

The target venues require accurate, traceable references and disclosure of AI
use in research activities where relevant. Maintain a research log that records
how AI assistance was used for code, experiment design, data collection,
analysis, and manuscript preparation. Human authors remain responsible for all
claims, citations, data, and artifacts.
