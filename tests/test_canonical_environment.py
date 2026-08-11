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


def _read(path: Path) -> str:
    assert path.is_file(), f"missing canonical environment file: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


REAL_CORPUS_PATH = ("research", "defects", "real_corpus_v1")
READ_METHODS = {"read_bytes", "read_text", "open"}


def _path_parts(value: str) -> tuple[str, ...]:
    return tuple(part for part in re.split(r"[\\/]+", value) if part and part != ".")


def _contains_parts(parts: tuple[str, ...], expected: tuple[str, ...]) -> bool:
    width = len(expected)
    return any(parts[index : index + width] == expected for index in range(len(parts)))


def _contains_file_reference(node: ast.AST) -> bool:
    return any(isinstance(child, ast.Name) and child.id == "__file__" for child in ast.walk(node))


def _path_value(
    node: ast.AST, bindings: dict[str, tuple[bool, tuple[str, ...]]]
) -> tuple[bool, tuple[str, ...]] | None:
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        parts = _path_parts(node.value)
        return (True, parts) if _contains_parts(parts, REAL_CORPUS_PATH) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        base = _path_value(node.left, bindings)
        if base is None or not isinstance(node.right, ast.Constant):
            return None
        if not isinstance(node.right.value, str):
            return None
        return (base[0], (*base[1], *_path_parts(node.right.value)))
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id == "str" and node.args:
            return _path_value(node.args[0], bindings)
        is_path_constructor = (
            isinstance(node.func, ast.Name) and node.func.id == "Path"
        ) or (isinstance(node.func, ast.Attribute) and node.func.attr == "Path")
        if is_path_constructor and node.args:
            if _contains_file_reference(node.args[0]):
                return (True, ())
            return _path_value(node.args[0], bindings)
    if _contains_file_reference(node):
        return (True, ())
    return None


def _is_protected_repo_path(
    node: ast.AST, bindings: dict[str, tuple[bool, tuple[str, ...]]]
) -> bool:
    value = _path_value(node, bindings)
    if value is None or not value[0] or not _contains_parts(value[1], REAL_CORPUS_PATH):
        return False
    corpus_index = next(
        index
        for index in range(len(value[1]))
        if value[1][index : index + len(REAL_CORPUS_PATH)] == REAL_CORPUS_PATH
    )
    artifact_parts = value[1][corpus_index + len(REAL_CORPUS_PATH) :]
    return "private" in artifact_parts or any(
        part.endswith(".private.json") for part in artifact_parts
    )


def _assigned_names(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [name for element in target.elts for name in _assigned_names(element)]
    return []


class _PrivateRepoReadVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.bindings: dict[str, tuple[bool, tuple[str, ...]]] = {}
        self.reads_private_repo_data = False

    def _bind(self, targets: list[ast.AST], value: ast.AST) -> None:
        path = _path_value(value, self.bindings)
        for target in targets:
            for name in _assigned_names(target):
                if path is None:
                    self.bindings.pop(name, None)
                else:
                    self.bindings[name] = path

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        self._bind(node.targets, node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)
            self._bind([node.target], node.value)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id == "open" and node.args:
            self.reads_private_repo_data |= _is_protected_repo_path(
                node.args[0], self.bindings
            )
        elif isinstance(node.func, ast.Attribute):
            if (
                node.func.attr == "open"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "builtins"
                and node.args
            ):
                self.reads_private_repo_data |= _is_protected_repo_path(
                    node.args[0], self.bindings
                )
            elif node.func.attr in READ_METHODS:
                self.reads_private_repo_data |= _is_protected_repo_path(
                    node.func.value, self.bindings
                )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return


def _test_function_reads_private_repo_data(node: ast.AST) -> bool:
    assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    visitor = _PrivateRepoReadVisitor()
    for statement in node.body:
        visitor.visit(statement)
    return visitor.reads_private_repo_data


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
            if not _test_function_reads_private_repo_data(node):
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


def _fixture_test_function(body: str) -> ast.FunctionDef:
    source = "def test_reader(tmp_path):\n" + "\n".join(
        f"    {line}" for line in body.splitlines()
    )
    function = ast.parse(source).body[0]
    assert isinstance(function, ast.FunctionDef)
    return function


def test_ast_read_sink_requires_a_marker_for_a_direct_real_private_artifact_read() -> None:
    function = _fixture_test_function(
        "\n".join(
            [
                'root = Path(__file__).parents[1] / "research" / "defects" / "real_corpus_v1"',
                'payload = (root / "selected_candidates.private.json").read_text()',
            ]
        )
    )

    assert _test_function_reads_private_repo_data(function)


def test_ast_read_sink_ignores_a_tmp_private_named_file() -> None:
    function = _fixture_test_function(
        'payload = (tmp_path / "x.private.json").read_text()'
    )

    assert not _test_function_reads_private_repo_data(function)


def test_ast_read_sink_ignores_a_protected_path_without_a_read() -> None:
    function = _fixture_test_function(
        "\n".join(
            [
                'candidate = Path(__file__).parents[1] / "research" / "defects" / "real_corpus_v1" / "private" / "selected.json"',
                "assert candidate.exists()",
            ]
        )
    )

    assert not _test_function_reads_private_repo_data(function)


def test_ast_read_sink_tracks_an_assigned_real_private_artifact_path() -> None:
    function = _fixture_test_function(
        "\n".join(
            [
                'root = Path(__file__).parents[1] / "research" / "defects" / "real_corpus_v1"',
                'private_path = root / "selected_candidates.private.json"',
                "payload = private_path.read_bytes()",
            ]
        )
    )

    assert _test_function_reads_private_repo_data(function)


def test_ast_read_sink_detects_builtins_open_for_a_real_private_artifact() -> None:
    function = _fixture_test_function(
        "\n".join(
            [
                'root = Path(__file__).parents[1] / "research" / "defects" / "real_corpus_v1"',
                'payload = builtins.open(root / "selected_candidates.private.json").read()',
            ]
        )
    )

    assert _test_function_reads_private_repo_data(function)


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
