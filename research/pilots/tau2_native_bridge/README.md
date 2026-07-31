# tau2 Native Grader/State Bridge Pilot

Status: deterministic engineering Pilot; not confirmatory evidence.

This Pilot binds the eight preregistered tau2 retail Pilot tasks to the native
`EnvironmentEvaluator`, task-derived pre/gold/predicted DB deltas, and semantic
side-effect ledgers. It executes the benchmark's reference actions solely to
validate bridge plumbing. Those actions remain a non-normative plan and these
results are not an Agent performance estimate.

The runtime must be an isolated Python 3.12 environment containing the exact
tau2 Git revision `fc0055dc4e0a316c3f83133267fbd6faaa770992` (`v1.0.1`). Run:

```bash
uv venv .venv-tau2 --python 3.12
uv pip install --python .venv-tau2/bin/python -e . \
  'tau2 @ git+https://github.com/sierra-research/tau2-bench.git@fc0055dc4e0a316c3f83133267fbd6faaa770992'
.venv-tau2/bin/python research/pilots/tau2_native_bridge/run.py \
  --output-dir /tmp/tau2-native-bridge
```

The runner rejects version, Git revision, and `tasks.json` checksum drift. It
emits the bound Benchmark Manifest, raw Scenario reports, a claim-bounded
summary, and SHA-256 checksums.
