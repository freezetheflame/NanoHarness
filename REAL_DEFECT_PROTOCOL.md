# Real-Defect Corpus Protocol for Agent Testing

English | [中文](REAL_DEFECT_PROTOCOL_CN.md) | [Paper plan](PAPER_PLAN.md)

Status: **draft protocol, venue decision pending**. Protocol version:
`1.0-draft`. Thresholds, repositories, time windows, search strings, and the
partition rule must be preregistered before systematic collection starts.

## Purpose and claim boundary

The corpus supports RQ1: whether proposed agent-specific mutation operators
represent defects observed in real agent systems. A record marked `candidate`
is an item awaiting review, not empirical evidence. Only `verified` records may
enter defect counts, operator-support claims, or held-out representativeness
results.

The unit of analysis is one implementation defect with one root cause. Multiple
reports of the same root cause are merged and retain `duplicate_of` links.
Several defects fixed by one pull request remain separate when their root causes
are distinct.

## Source sampling

Before retrieval, freeze a sampling manifest containing:

- repository names and immutable repository revisions;
- collection start/end dates and defect-fix time window;
- exact issue labels, search strings, API queries, and pagination limits;
- inclusion of closed issues without fixes and commit-only defects;
- the deduplication and corpus-partition rule.

Collect from NanoHarness and independently developed agent runtimes. Frameworks
must be selected by an explicit criterion such as public history, tool-use
support, activity, and availability of tests—not because their issues already
fit an operator. Preserve the retrieval output before screening so the candidate
flow and exclusion counts can be reconstructed.

## Inclusion and exclusion

Include an item when all of the following hold:

1. it describes incorrect implemented behavior rather than a feature request;
2. it affects an agent runtime, harness, tool boundary, Context/state handling,
   policy/permission path, lifecycle, evaluator, or replay behavior;
3. the trigger and externally observable symptom can be stated;
4. stable evidence identifies the report, reproduction, test, or fixing change;
5. it falls inside the preregistered repository and time window.

Exclude and retain the reason when the item is documentation-only, an unsupported
usage question, solely an upstream outage, unreproducible with no confirmatory
evidence, outside scope, or a duplicate. Security defects remain eligible when
they arise from an agent execution or policy boundary; they are not excluded
merely for being security-related.

## Evidence standard

A verified defect must contain:

- a stable repository and defect identifier;
- a concise symptom, trigger, root cause, and impact;
- at least one affected component boundary;
- at least two distinct evidence roles;
- at least one fixing change, regression test, or reproduction;
- a final inclusion rationale and a derivation/validation partition.

For every evidence item, record a URL or local locator, immutable revision when
available, file/test identifier, access date for mutable pages, and an archive
hash when licensing permits archiving. A fixing commit by itself does not prove
that an issue report described the same failure; coders must inspect the linked
artifacts.

## Leakage-resistant partitioning

The derivation partition is used to develop and freeze the operator taxonomy.
The validation partition is held out until operator names, preconditions, and
semantics are frozen. Validation defects must not be used to add or revise an
operator before the preregistered RQ1 analysis.

Use one preregistered rule across all candidates. A temporal split is preferred:
older resolved defects form derivation data and newer defects form validation
data, with the cutoff chosen before coding. If history is too sparse, use a
repository-stratified deterministic hash split and publish the code and seed.
Never manually move a difficult validation defect into derivation.

RQ1 reports the fraction of verified validation defects mapped to at least one
frozen operator, per-boundary mapping rates, defects requiring operator
composition, and unmapped-root-cause categories. Results on derivation defects
are descriptive and cannot establish external representativeness.

## Independent coding and adjudication

Use two human coders for every candidate when feasible. AI may help retrieve or
summarize sources, but it is not counted as an independent human coder and its
role must be disclosed.

Coding has two passes:

1. **Pass A, operator-blind:** decide include/exclude/uncertain and label symptom,
   root cause, trigger, impact, and component boundaries without seeing proposed
   operator mappings.
2. **Pass B, frozen catalog:** map included defects to zero or more operators and
   record why the preconditions and semantics match.

Retain both raw annotations. Resolve disagreements by documented consensus or a
third human adjudicator; never overwrite the independent records. Report raw
agreement before adjudication: Cohen's kappa for inclusion decisions and mean
Jaccard agreement for boundary and operator labels. Also report the double-coded
sample size and category prevalence because kappa is prevalence-sensitive.

## Freeze and audit gates

Before setting `frozen_at`, all candidates must be `verified` or `excluded` and
the preregistered minimums must pass. `DefectCorpusAnalyzer.assess_readiness`
checks:

- minimum verified and held-out validation counts;
- minimum double-coded count;
- unresolved candidates or disputes;
- frozen operators without a motivating verified derivation defect.

The machine-readable corpus is stored at `research/defects/corpus.json`. Its
current seed record is deliberately marked `candidate`; it demonstrates the
pipeline but must not appear in paper results until human coding and adjudication
are complete.

## Reproducible outputs

The replication package must include the sampling manifest, raw retrieval data
where redistribution is allowed, screening decisions and exclusion reasons,
independent annotations, adjudication log, frozen corpus JSON, operator catalog,
analysis command, and generated summary tables. Public reports may require
redaction, but stable IDs must allow authorized auditors to reconnect records to
their preserved sources.
