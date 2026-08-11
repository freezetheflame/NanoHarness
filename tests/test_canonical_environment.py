from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "scripts" / "bootstrap_canonical_env.ps1"
VERIFY = ROOT / "scripts" / "verify_canonical_env.ps1"
TESTS = ROOT / "tests"
PRIVATE_REPO_DATA_MARKER = "private_repo_data"
PRIVATE_REPO_DATA_PATH = re.compile(
    r"(?:research[\\/]+defects[\\/]+real_corpus_v1[\\/]+private[\\/]+|\.private\.json\b)"
)


def _read(path: Path) -> str:
    assert path.is_file(), f"missing canonical environment file: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def _private_repo_data_readers() -> dict[str, bool]:
    readers: dict[str, bool] = {}
    for test_path in TESTS.rglob("*.py"):
        source = _read(test_path)
        tree = ast.parse(source, filename=str(test_path))
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            function_source = ast.get_source_segment(source, node) or ""
            if not PRIVATE_REPO_DATA_PATH.search(function_source):
                continue
            classes: list[str] = []
            ancestor = parents[node]
            while ancestor is not tree:
                if isinstance(ancestor, ast.ClassDef):
                    classes.append(ancestor.name)
                ancestor = parents[ancestor]
            node_id = "::".join(
                [test_path.relative_to(ROOT).as_posix(), *reversed(classes), node.name]
            )
            decorators = list(node.decorator_list)
            ancestor = parents[node]
            while ancestor is not tree:
                if isinstance(ancestor, ast.ClassDef):
                    decorators.extend(ancestor.decorator_list)
                ancestor = parents[ancestor]
            readers[node_id] = any(
                isinstance(decorator.func if isinstance(decorator, ast.Call) else decorator, ast.Attribute)
                and (decorator.func if isinstance(decorator, ast.Call) else decorator).attr
                == PRIVATE_REPO_DATA_MARKER
                for decorator in decorators
            )
    return readers


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
    assert "$sourcePythonVersion = & $sourcePython" in text
    assert "$actualVersion = & $venvPython" in text
    assert "import sys; print(sys.version.split()[0])" in text
    assert "Unexpected source interpreter" in text
    assert "Unexpected interpreter" in text
    assert "$LASTEXITCODE" in text and "exit $LASTEXITCODE" in text


@pytest.mark.parametrize("script", [BOOTSTRAP, VERIFY])
def test_canonical_scripts_reject_non_windows_or_legacy_powershell(script: Path) -> None:
    text = _read(script)
    assert "$IsWindows" in text
    assert "$PSVersionTable.PSVersion.Major -lt 7" in text
    assert "Windows PowerShell 7 or newer is required" in text


def test_bootstrap_attestation_rejects_an_unexpected_created_interpreter(
    tmp_path: Path,
) -> None:
    fake_root = tmp_path / "repo"
    fake_scripts = fake_root / "scripts"
    fake_bin = tmp_path / "bin"
    fake_source = tmp_path / "python-3.12.2.cmd"
    fake_python = fake_root / ".venv" / "Scripts" / "python.cmd"
    fake_scripts.mkdir(parents=True)
    fake_bin.mkdir()
    fake_python.parent.mkdir(parents=True)
    fake_source.write_text("@echo off\r\necho 3.12.2\r\n", encoding="utf-8")
    fake_python.write_text("@echo off\r\necho 3.12.5\r\n", encoding="utf-8")
    fake_uv = fake_bin / "uv.cmd"
    fake_uv.write_text(
        f'@echo off\r\nif "%1 %2"=="python find" echo {fake_source}\r\nexit /b 0\r\n',
        encoding="utf-8",
    )
    script_text = _read(BOOTSTRAP).replace(
        '".venv\\Scripts\\python.exe"', '".venv\\Scripts\\python.cmd"'
    )
    copied_bootstrap = fake_scripts / BOOTSTRAP.name
    copied_bootstrap.write_text(script_text, encoding="utf-8")
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(copied_bootstrap)],
        cwd=fake_root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode != 0
    assert "Unexpected interpreter" in result.stderr


def test_bootstrap_rejects_an_unexpected_source_before_creating_a_venv(
    tmp_path: Path,
) -> None:
    fake_root = tmp_path / "repo"
    fake_scripts = fake_root / "scripts"
    fake_bin = tmp_path / "bin"
    fake_source = tmp_path / "python-3.12.5.cmd"
    command_log = tmp_path / "uv-commands.log"
    fake_scripts.mkdir(parents=True)
    fake_bin.mkdir()
    fake_source.write_text("@echo off\r\necho 3.12.5\r\n", encoding="utf-8")
    fake_uv = fake_bin / "uv.cmd"
    fake_uv.write_text(
        "@echo off\r\n"
        f'echo %*>>"{command_log}"\r\n'
        f'if "%1 %2"=="python find" echo {fake_source}\r\n'
        "exit /b 0\r\n",
        encoding="utf-8",
    )
    copied_bootstrap = fake_scripts / BOOTSTRAP.name
    copied_bootstrap.write_text(_read(BOOTSTRAP), encoding="utf-8")
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(copied_bootstrap)],
        cwd=fake_root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode != 0
    assert "Unexpected source interpreter" in result.stderr
    assert command_log.read_text(encoding="utf-8").splitlines() == ["python find 3.12.2"]


@pytest.mark.parametrize("script", [BOOTSTRAP, VERIFY])
def test_canonical_scripts_reject_legacy_powershell_at_runtime(script: Path) -> None:
    result = subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-Command",
            f'$PSVersionTable.PSVersion = [version]"5.1"; & "{script}"; exit $LASTEXITCODE',
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode != 0
    assert "PowerShell 7 or newer is required" in result.stderr


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


def test_private_repo_data_marker_is_registered_and_used_by_full_tier() -> None:
    project = _read(ROOT / "pyproject.toml")
    text = _read(VERIFY)
    assert f"{PRIVATE_REPO_DATA_MARKER}:" in project
    assert '--strict-markers -m "not private_repo_data"' in text
    assert "--deselect=" not in text


def test_every_direct_private_repo_reader_is_marked() -> None:
    readers = _private_repo_data_readers()
    assert readers
    assert all(readers.values()), readers


def test_private_repo_data_marker_collects_only_static_readers() -> None:
    env = os.environ.copy()
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    command = [
        sys.executable,
        "-m",
        "pytest",
        "--collect-only",
        "tests",
        "-q",
        "--strict-markers",
        "-m",
        PRIVATE_REPO_DATA_MARKER,
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    collected = {
        line for line in result.stdout.splitlines() if line.startswith("tests/")
    }
    assert collected == set(_private_repo_data_readers())


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
