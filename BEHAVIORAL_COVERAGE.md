# Behavioral Coverage Protocol for Agent Tests

English | [中文](BEHAVIORAL_COVERAGE_CN.md) | [Paper plan](PAPER_PLAN.md)

Status: **initial implementation, empirical validation pending**. This protocol
defines the first metric used in RQ3. It does not claim that covered behavior is
correct or that a higher percentage always implies a stronger test suite.

## Explicit denominator

Every subject has a versioned `CoverageModel` listing stable `CoverageTarget`
IDs before the measured executions begin. The total number of enabled targets
is the denominator. The collector never creates targets from observed traces:
an inferred universe would make every observed-only test suite appear fully
covered and would prevent meaningful comparison.

Targets should be derived from the subject's public tool schemas, runtime state
machine, policy rules, fault catalog, and test requirements. Changes to the
universe create a new model version. Publish both the model and its derivation
procedure. A draft template is not a frozen experimental denominator.

A frozen model binds a subject/revision, derivation description, timezone-aware
freeze time, and SHA-256 digest of the ordered target universe. Experiment code
uses `require_frozen=True`; this rejects templates, drafts, and targets changed
after freezing.

## Implemented dimensions

The first collector supports evidence that current stable contracts expose:

- tool calls;
- tool-argument equivalence classes (`present`, `missing`, `equals`, `one_of`,
  JSON `type`, numeric `range`, and `regex`);
- Context message roles, state save/load value classes, hook-stage outcomes,
  and permission allow/deny decisions;
- `RunStatus` and `StopReason`;
- lifecycle event types;
- model, tool, and hook error events;
- normalized execution errors;
- deterministic Oracle pass/fail outcomes;
- injected fault actions and whether the associated Scenario passed;
- declared Trace metadata such as baseline/fault-injected execution mode.

Full state-machine transitions, checkpoint restore/recovery paths, retry/time
events, and untrusted-source-to-sensitive-sink flows are not yet covered because
NanoHarness does not expose stable normalized events for all of them. They must
be added as runtime contracts before those dimensions can enter a denominator.
Absence of a target is not evidence that such behavior was tested.

## Evidence and aggregation

Each hit retains Scenario ID, Trace ID, source, and event ID/sequence when an
event exists. Evidence details are normalized and use default sensitive-field
redaction. Repeated hits increase `hit_count` but do not increase the number of
covered targets.

Aggregation is set union over the same immutable model. Report:

- covered/total targets and ratio;
- covered/total and ratio for every dimension;
- uncovered target IDs and uncovered required target IDs;
- hit counts and supporting execution evidence.

Do not pool target counts from incompatible subject models into one micro ratio;
large models would dominate. Report each subject and dimension separately and,
when a cross-subject summary is justified, include a macro average with
uncertainty and the individual values.

## Fault and recovery interpretation

A `fault_action` hit proves that a configured rule was applied; it does not prove
the agent observed or recovered from the fault. A `fault_outcome` target combines
an applied rule with `ScenarioReport.passed`. It may be called recovery coverage
only when the Scenario contains deterministic Oracles that directly validate the
specified recovery invariant. Warning-only failures must be analyzed separately
because they do not make the Scenario fail.

## Thresholds and RQ3

Coverage has no default pass threshold. `CoverageReport.evaluate_gate` enforces
only required targets and a ratio explicitly selected by the user. The paper
must not recommend a threshold until empirical evidence relates it to real
defect detection.

For RQ3, compare task success, behavioral coverage, and mutation score on paired
test suites/scenarios. Freeze coverage models and mutation operators before the
held-out analysis. Report correlations with confidence intervals and inspect
discordant cases—for example, high coverage with surviving mutants or low
coverage despite task success. Do not treat repeated runs or mutants from one
Scenario as statistically independent observations.
