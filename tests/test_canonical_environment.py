from __future__ import annotations

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "scripts" / "bootstrap_canonical_env.ps1"
VERIFY = ROOT / "scripts" / "verify_canonical_env.ps1"


def _read(path: Path) -> str:
    assert path.is_file(), f"missing canonical environment file: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def test_python_version_is_pinned_exactly() -> None:
    assert _read(ROOT / ".python-version") == "3.12.2\n"


def test_project_supports_the_pinned_python_and_declares_lockable_extras() -> None:
    project = _read(ROOT / "pyproject.toml")
    assert 'requires-python = ">=3.12,<3.13"' in project
    for extra in ("dev", "research", "agentdojo-research"):
        assert re.search(rf"(?m)^{re.escape(extra)}\s*=\s*\[", project)


def test_uv_lock_is_committed_for_the_exact_python_pin() -> None:
    lock = _read(ROOT / "uv.lock")
    assert 'requires-python = "==3.12.*"' in lock
    assert 'name = "nanoharness"' in lock


@pytest.mark.parametrize("script", [BOOTSTRAP, VERIFY])
def test_canonical_scripts_never_invoke_bare_python_pytest_or_pip(script: Path) -> None:
    text = _read(script)
    forbidden = re.compile(
        r"(?im)^\s*(?:&\s*)?(?:python(?:\.exe)?|pytest(?:\.exe)?|pip(?:\.exe)?)\b"
    )
    assert not forbidden.search(text)
    assert ".venv\\Scripts\\python.exe" in text


def test_bootstrap_resolves_repo_root_and_performs_frozen_sync() -> None:
    text = _read(BOOTSTRAP)
    assert "$PSScriptRoot" in text
    assert "uv python find 3.12.2" in text
    assert "uv python install 3.12.2" in text
    assert "uv venv --python $sourcePython" in text
    assert "--allow-existing" in text
    assert "uv sync --frozen" in text
    assert "$LASTEXITCODE" in text and "exit $LASTEXITCODE" in text


def test_verifier_enforces_interpreter_health_and_test_isolation() -> None:
    text = _read(VERIFY)
    assert "$PSScriptRoot" in text
    assert "3.12.2" in text
    assert "unexpected interpreter" in text.lower()
    assert "uv pip check" in text
    assert "$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = \"1\"" in text
    assert "Focused" in text and "Full" in text
    assert "tests/test_canonical_environment.py" in text
    assert "$LASTEXITCODE" in text and "exit $LASTEXITCODE" in text


@pytest.mark.parametrize("readme", [ROOT / "README.md", ROOT / "README_CN.md"])
def test_documentation_declares_the_single_windows_canonical_path(readme: Path) -> None:
    text = _read(readme)
    for required in (
        "PowerShell 7",
        "uv",
        ".venv\\Scripts\\python.exe",
        "bootstrap_canonical_env.ps1",
        "verify_canonical_env.ps1",
        "requirements.txt",
        "WSL",
        "tau2",
    ):
        assert required in text
    assert re.search(r"(?is)(canonical|规范).*bare.*(?:python|pytest|pip)", text)
    assert ".venv/bin/python" not in text
    assert not re.search(r"(?m)^\s*(?:python|pytest|pip)\b", text)
