[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required. Install uv, then rerun this script in PowerShell 7."
}

Push-Location $repoRoot
try {
    $sourcePython = & uv python find 3.12.2 2>$null
    if ($LASTEXITCODE -ne 0) {
        & uv python install 3.12.2
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        $sourcePython = & uv python find 3.12.2
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    & uv venv --python $sourcePython --allow-existing .venv
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        throw "Canonical interpreter was not created at $venvPython"
    }
    & uv sync --frozen --python $venvPython --extra dev --extra research --extra agentdojo-research
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}
