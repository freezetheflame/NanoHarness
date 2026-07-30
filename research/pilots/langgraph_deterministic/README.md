# Deterministic LangGraph Adapter Pilot

This pilot validates the external-subject adapter, frozen-manifest integrity,
real LangGraph execution, normalized Trace production, Oracle evaluation, and
raw-result serialization. It is **not** a paper-scale external-validity or
mutation experiment.

## Frozen provenance

- Runtime: `langgraph==1.2.10`
- Runtime source: <https://github.com/langchain-ai/langgraph>
- Subject revision: `pypi:1.2.10`
- NanoHarness revision:
  `10a5cb24e97e0a032f8ec289e919da60d811d0be`
- Manifest digest:
  `38172c288e5d95a0a374713d768a5ec9517a798dc134a7b8a6afe7bb75f23b8a`

The manifest contains two deterministic StateGraph scenarios and three
preregistered seeds per scenario. The graph creates fresh compiled state for
every observation and streams full `values` snapshots; the Adapter does not
attempt to reconstruct LangGraph reducer semantics from partial updates.

## Reproduction

From the repository root:

```bash
uv pip install --python .venv/bin/python -e '.[research]'
.venv/bin/python research/pilots/langgraph_deterministic/run.py \
  --output /tmp/langgraph-pilot-report.json
```

The archived run at `raw/pilot_report.json` contains six observations: six
passed, zero execution errors, and a descriptive pass rate of `1.0`. Its
SHA-256 is:

```text
01e44e7a621bf95a7798a72fc59f2e56d7ce99e2a3c36b143cd74e905ece4094
```

Wall-clock timestamps, durations, and UUID Trace IDs intentionally make a new
raw report byte-different. Reproduction checks the frozen Manifest, observation
count/order, seeds, final states, Verdicts, and summary—not equality of runtime
noise.

## Claim boundary

The graph is a small deterministic echo workflow without a live model, tool
side effects, mutation campaign, or Benchmark dataset. This run proves only
that the external Adapter and replication plumbing execute against the pinned
real Runtime. It cannot support claims about LangGraph reliability, mutation
score, behavioral coverage validity, cost, flakiness, or generality. Those need
paper-scale tool-using subjects and held-out scenarios.
