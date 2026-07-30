# AgentDojo Offline Conversion Pilot

This pilot validates a pinned external benchmark data source, loss-aware
Task-to-Scenario conversion, frozen provenance, and machine-readable artifact
integrity. It is an integration check, not an AgentDojo result reproduction or
a mutation-testing experiment.

## Frozen provenance and selection

- Distribution: `agentdojo==0.1.35`
- Benchmark version: `v1.2.2`
- Source: <https://github.com/ethz-spylab/agentdojo>
- Git revision: `a75aba7631d3ca5fb7ab938965c97ead2f9ff84b`
- License: MIT
- Workspace manifest digest:
  `f092448d01872a1de988cd06e45bdc3338a809b3f54a45e68073f9f1d1dbd4d4`
- Banking manifest digest:
  `848204a82685fb695a8bf05b83ce2d3e97844b112e77379d7908b5414785f1b7`

The development-only selection contains four user tasks:

- Workspace: `user_task_0`, `user_task_16`
- Banking: `user_task_1`, `user_task_6`

These task IDs are not a held-out validation sample. They exercise read-only
and state-changing reference plans while keeping the pilot inspectable.

## Conversion contract

Each AgentDojo prompt becomes a serializable NanoHarness `Scenario`. The frozen
record retains the initialized pre-environment digest, task difficulty,
ground-truth output, reference `FunctionCall` values, and the original utility
scorer's module-qualified provenance.

The conversion deliberately does **not** turn the AgentDojo reference call list
into an exact tool-call Oracle. Alternative plans may be valid. AgentDojo's
utility scorer also requires its own pre/post environment semantics, which an
offline conversion cannot preserve during arbitrary subject execution.
Consequently every converted Scenario contains no Oracles and is explicitly
marked `oracle_binding: unbound`; reference calls are labelled
`non_normative_reference_plan`.

## Reproduction

From the repository root:

```bash
uv pip install --python .venv/bin/python -e '.[agentdojo-research]'
.venv/bin/python research/pilots/agentdojo_offline_conversion/run.py \
  --output-dir /tmp/agentdojo-conversion
```

The archived JSON SHA-256 values are:

```text
bdf616930bd3d9bc2ff6fe04433edf26dcbc6899f8a76a9142944f5bdf4ba69f  banking_manifest.json
c487fc22b94f2e67020d7058f4881d169e1a11d65f4b0a89879e554c84156a3a  workspace_manifest.json
c0306e346a9f558561d77b8ce79eee09849aaf0c055f13d39b7f2dccef8bc828  conversion_summary.json
```

## Claim boundary

No live model is invoked, no AgentDojo agent pipeline is executed, and no
utility or security score is computed. The four task conversions therefore do
not establish model task success, prompt-injection resistance, mutation score,
Oracle adequacy, external validity, or reproduction of AgentDojo's published
results. They establish only that the pinned public tasks and their reference
data can enter the NanoHarness experimental pipeline without erasing source or
scorer provenance. A paper-scale experiment still requires a runtime bridge
that preserves AgentDojo environment transitions and invokes the original
scorer, plus a preregistered held-out task selection.
