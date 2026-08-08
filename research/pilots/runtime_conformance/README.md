# M1--M4 Cross-Runtime Conformance Evidence

Status: executed deterministic engineering validation; not confirmatory paper
evidence for mutation-score effects or Agent performance.

## Purpose

This experiment executes the four runtime-neutral cases defined by the paper
under both NanoHarness and the independently developed LangGraph runtime. It
retains each raw `ScenarioReport`, projects only declared cross-runtime semantic
facts, and compares those projections without rewriting runtime-private events.

## Frozen provenance

- Experiment: `m1-m4-runtime-conformance-v1`
- NanoHarness implementation revision:
  `1e138b50590b6e91d85f63e84db7ef53c0bd654a`
- NanoHarness package: `0.1.0`
- LangGraph package: `1.2.10`
- Python: `3.12.2`
- Manifest digest:
  `c504ffe68abdbc5f398d0aa25c68d88f770d52da64c2648fd93cb2671cd8c304`
- Live model, network service, benchmark grader, and external side effects:
  none

The retained report was produced from a clean checkout of the implementation
revision above. Its UUIDs and timestamps are runtime evidence, so an independent
rerun is expected to be semantically equal but byte-different.

## Cases and result

| Case | Declared semantics | NanoHarness | LangGraph | Paired comparison |
|---|---|---:|---:|---:|
| M1 | One typed successful Tool call and delivered Observation | pass | pass | pass |
| M2 | Tool error followed by the declared recovery | pass | pass | pass |
| M3 | Normal termination with `goal_achieved=false` | expected Goal-Oracle failure | expected Goal-Oracle failure | pass |
| M4 | Denied write with zero underlying Tool attempts | pass | pass | pass |

The archive contains eight runtime-case cells and four paired comparisons. All
four comparisons pass with zero field mismatches and zero infrastructure
errors. M3's `ScenarioReport.passed=false` is the preregistered outcome: the
case is conformant only when normal termination remains distinct from goal
achievement.

NanoHarness reports `model_terminated` and LangGraph reports
`subject_completed`. The frozen Manifest maps only these two values to the
shared semantic label `normal_termination`. Raw stop reasons remain unchanged
in the archived reports. No equivalence mapping is permitted for run status,
achievement, errors, tool attempts, permissions, lifecycle, or round-trip
invariants.

## Reproduction

From a clean NanoHarness checkout at the implementation revision:

```powershell
uv venv .venv --python 3.12
uv pip install --python .venv\Scripts\python.exe -e ".[research]"
uv pip install --python .venv\Scripts\python.exe pytest
.venv\Scripts\python.exe research\pilots\runtime_conformance\run.py `
  --output-dir $env:TEMP\m1m4-runtime-conformance-reproduction
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
.venv\Scripts\python.exe -m pytest `
  tests\test_conformance.py `
  tests\test_runtime_conformance_runner.py `
  tests\external\test_runtime_conformance_integration.py -q
```

The runner refuses to overwrite an existing output by default. Choose a fresh
directory for reproduction. Compare the regenerated semantic projections,
case order, verdicts, and summary rather than UUIDs, timestamps, durations, or
the raw-report byte hash.

## Retained files

- `manifest.json`: frozen cases, runtimes, comparison fields, and equivalence
  map.
- `raw/conformance_report.json`: eight unmodified runtime reports, eight
  projections, and four field-level comparisons.
- `summary.json`: auditable aggregate counts and per-case verdicts.
- `SHA256SUMS`: byte-level hashes over the frozen Manifest, raw report, and
  summary.

## Claim boundary

This result supports only the engineering claim that the common Scenario,
Trace, Oracle, and semantic-projection contracts executed consistently for four
preregistered deterministic cases under the pinned runtimes. It does not show
that mutation operators represent held-out real defects, that T2 outperforms
T0/T1, that mutation score complements behavioral coverage, or that Replay
reduces calls, latency, or cost. No population mutants, live model, stochastic
repetitions, or confirmatory estimators were used.
