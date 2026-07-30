# LangGraph Executable Tool-Fault Pilot

This Pilot validates executable Agent-specific faults inside a pinned real
LangGraph StateGraph. It is an integration and measurement-semantics check, not
a confirmatory mutation-testing result.

## Frozen provenance

- Runtime: `langgraph==1.2.10`
- Subject revision: `pypi:1.2.10`
- NanoHarness/subject revision:
  `457922a4beccf9372fb5daf39068389ad78395ed`
- Manifest digest:
  `b9269b84e0f29400de90b6aa2b29ff2aea92ee6c9ded9b0dd048d8d1e1abe01e`
- Raw Campaign SHA-256:
  `854add94ab1d9f2e7bcd765f72f94d4b58d63e2f9f3ddb1b2c524b2f740798c1`

The graph contains deterministic plan, tool, and answer nodes. Its `echo` tool
returns both content and a call counter so duplicate execution is externally
observable. The frozen Scenario composes goal, run/stop, exact tool argument,
tool count, tool result, and lifecycle Oracles.

## Fault outcomes

| Plan | Status | Detection evidence |
|---|---|---|
| stale tool result | killed | wrong goal result and delivered tool result |
| duplicate tool call | killed | `attempt_count=2` and second-call result |
| dropped required argument | killed | actual empty arguments, tool error, and incomplete lifecycle |

The Pilot Mutation Score is `3 / (3 + 0) = 1.0`. This number is descriptive
plumbing output only. The three Mutants were selected to exercise the Adapter;
they are not a representative or held-out sample.

Underlying attempts use `TOOL_STARTED`/`TOOL_COMPLETED`/`TOOL_ERROR`; the single
`TOOL_EXCHANGE` records the Observation delivered to graph state plus cumulative
`attempt_count`. This avoids hiding duplicates or recording a fresh underlying
result when the Agent actually received a stale result.

## Reproduction

```bash
uv pip install --python .venv/bin/python -e '.[research]'
.venv/bin/python research/pilots/langgraph_tool_faults/run.py \
  --output /tmp/langgraph-tool-fault-campaign.json
```

The archived file is `raw/fault_campaign.json`. New runs differ in UUIDs,
timestamps, and duration values; compare frozen semantic fields rather than raw
bytes.

## Claim boundary

This Pilot uses one handcrafted deterministic graph, one local tool, one
Scenario, and three Tool-boundary faults. It has no live model, Benchmark
dataset, real-defect sampling, stochastic repetitions, multi-Agent behavior, or
external side effects. It proves that a real LangGraph graph can participate in
the executable Campaign and that attempt/Observation evidence is internally
consistent. It does not establish Operator representativeness, generality, or a
paper Mutation Score.
