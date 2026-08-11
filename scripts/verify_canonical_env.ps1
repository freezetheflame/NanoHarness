[CmdletBinding()]
param(
    [ValidateSet("Focused", "Full")]
    [string]$Tier = "Focused"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"

function Stop-OnFailure {
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    throw "Canonical interpreter is missing at $venvPython. Run scripts/bootstrap_canonical_env.ps1 first."
}

Push-Location $repoRoot
try {
    $actualVersion = & $venvPython -c "import platform; print(platform.python_version())"
    Stop-OnFailure
    if ($actualVersion.Trim() -ne "3.12.2") {
        throw "Unexpected interpreter version '$actualVersion'; canonical Python is exactly 3.12.2."
    }

    & uv pip check --python $venvPython
    Stop-OnFailure

    $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
    if ($Tier -eq "Focused") {
        & $venvPython -m pytest `
            tests/test_canonical_environment.py `
            tests/test_schema.py `
            tests/test_engine.py `
            tests/test_runtime_conformance_runner.py `
            tests/test_agentdojo_benchmark.py `
            tests/test_tau2_benchmark.py `
            -q
    }
    else {
        & $venvPython -m pytest tests -q
    }
    Stop-OnFailure
}
finally {
    Pop-Location
}
