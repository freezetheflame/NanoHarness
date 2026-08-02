# tau2 Native Grader/State Bridge Pilot

Status: deterministic engineering Pilot; not confirmatory evidence.

This Pilot binds the eight preregistered tau2 retail Pilot tasks to the native
`EnvironmentEvaluator`, task-derived pre/gold/predicted DB deltas, and semantic
side-effect ledgers. It executes the benchmark's reference actions solely to
validate bridge plumbing. Those actions remain a non-normative plan and these
results are not an Agent performance estimate.

The runtime must be an isolated Python 3.12 environment containing an editable
checkout of the exact tau2 Git revision
`fc0055dc4e0a316c3f83133267fbd6faaa770992` (`v1.0.1`). The editable checkout
is required because tau2 resolves its benchmark data relative to the source
tree and this runner verifies that tree's Git revision. Run:

```bash
git -c core.autocrlf=false clone --branch v1.0.1 --single-branch \
  https://github.com/sierra-research/tau2-bench.git ../tau2-bench-v1.0.1
git -C ../tau2-bench-v1.0.1 config core.autocrlf false
test "$(sha256sum ../tau2-bench-v1.0.1/data/tau2/domains/retail/tasks.json | cut -d ' ' -f 1)" = \
  8e03ebce7901bd6218e7a7dc3105faa9324091a68058f7fe61c65262868812e8
uv venv .venv-tau2 --python 3.12
uv pip install --python .venv-tau2/bin/python -e . -e ../tau2-bench-v1.0.1
uv pip install --python .venv-tau2/bin/python 'pytest>=7.0'
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv-tau2/bin/python -m pytest \
  tests/external/test_tau2_integration.py -q
.venv-tau2/bin/python research/pilots/tau2_native_bridge/run.py \
  --output-dir /tmp/tau2-native-bridge
```

On Windows, use `.venv-tau2/Scripts/python.exe` and verify the same checksum
with `Get-FileHash`. Do not use a checkout produced with automatic CRLF
conversion: the frozen task-set checksum covers the original LF bytes.

The runner rejects version, Git revision, and `tasks.json` checksum drift. It
emits the bound Benchmark Manifest, raw Scenario reports, a claim-bounded
summary, and SHA-256 checksums.

## Archived execution

The `raw/2026-08-02/` snapshot was executed on Windows with Python 3.12.11.
All eight reports passed T0-DB, state-delta, and side-effect Oracles. Its
Manifest digest is
`f61c4c4d14c2c41762c388364a433129da2e51f6f7c9f70ff1b78245eaf818c6`.
This remains engineering evidence for native bridge plumbing only.
