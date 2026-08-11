[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
if (-not $IsWindows -or $PSVersionTable.PSVersion.Major -lt 7) {
    Write-Error "Windows PowerShell 7 or newer is required for the canonical environment."
    exit 2
}

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
    $sourcePythonVersion = & $sourcePython -c "import sys; print(sys.version.split()[0])"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    if ($sourcePythonVersion.Trim() -ne "3.12.2") {
        throw "Unexpected source interpreter version '$sourcePythonVersion'; canonical Python is exactly 3.12.2."
    }
    & uv venv --python $sourcePython --allow-existing .venv
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        throw "Canonical interpreter was not created at $venvPython"
    }
    & uv sync --frozen --python $venvPython --extra dev --extra research --extra agentdojo-research
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $actualVersion = & $venvPython -c "import sys; print(sys.version.split()[0])"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    if ($actualVersion.Trim() -ne "3.12.2") {
        throw "Unexpected interpreter version '$actualVersion'; canonical Python is exactly 3.12.2."
    }
}
finally {
    Pop-Location
}
