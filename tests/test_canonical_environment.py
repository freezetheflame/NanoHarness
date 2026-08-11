from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
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


PathValue = tuple[bool, tuple[str, ...]]
PathEnvironment = dict[str, set[PathValue]]


def _path_values(node: ast.AST, bindings: PathEnvironment) -> set[PathValue]:
    if isinstance(node, ast.Name):
        return bindings.get(node.id, set())
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        parts = _path_parts(node.value)
        return {(_contains_parts(parts, REAL_CORPUS_PATH), parts)}
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Div)):
        left = _path_values(node.left, bindings)
        right = _path_values(node.right, bindings)
        return {
            (left_value[0] or right_value[0], (*left_value[1], *right_value[1]))
            for left_value in left
            for right_value in right
        }
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id == "str" and node.args:
            return _path_values(node.args[0], bindings)
        is_path_constructor = (
            isinstance(node.func, ast.Name) and node.func.id == "Path"
        ) or (isinstance(node.func, ast.Attribute) and node.func.attr == "Path")
        if is_path_constructor and node.args:
            if _contains_file_reference(node.args[0]):
                return {(True, ())}
            return _path_values(node.args[0], bindings)
    if _contains_file_reference(node):
        return {(True, ())}
    return set()


def _is_protected_repo_path(
    node: ast.AST, bindings: PathEnvironment
) -> bool:
    for anchored_to_repo, parts in _path_values(node, bindings):
        if not anchored_to_repo or not _contains_parts(parts, REAL_CORPUS_PATH):
            continue
        corpus_index = next(
            index
            for index in range(len(parts))
            if parts[index : index + len(REAL_CORPUS_PATH)] == REAL_CORPUS_PATH
        )
        artifact_parts = parts[corpus_index + len(REAL_CORPUS_PATH) :]
        if "private" in artifact_parts or any(
            part.endswith(".private.json") for part in artifact_parts
        ):
            return True
    return False


def _assigned_names(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [name for element in target.elts for name in _assigned_names(element)]
    return []


def _merge_path_environments(*environments: PathEnvironment) -> PathEnvironment:
    merged: PathEnvironment = {}
    for environment in environments:
        for name, values in environment.items():
            merged.setdefault(name, set()).update(values)
    return merged


def _copy_path_environment(environment: PathEnvironment) -> PathEnvironment:
    return {name: set(values) for name, values in environment.items()}


@dataclass
class _CallableFacts:
    reads_private_repo_data: bool = False
    calls: set[str] = field(default_factory=set)


class _CallableAnalyzer:
    def __init__(self, module_callables: set[str]) -> None:
        self.module_callables = module_callables
        self.facts = _CallableFacts()
        self.local_callables: set[str] = set()
        self.callable_aliases: dict[str, set[str]] = {}

    def analyze(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> _CallableFacts:
        self.local_callables = {
            child.name
            for child in ast.walk(node)
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child is not node
        }
        self.callable_aliases = {}
        self._analyze_statements(node.body, {})
        return self.facts

    def _bind(
        self, environment: PathEnvironment, targets: list[ast.AST], value: ast.AST
    ) -> None:
        values = _path_values(value, environment)
        aliases = self._callable_aliases(value)
        for target in targets:
            for name in _assigned_names(target):
                if values:
                    environment[name] = values
                else:
                    environment.pop(name, None)
                if aliases:
                    self.callable_aliases[name] = aliases
                else:
                    self.callable_aliases.pop(name, None)

    def _callable_aliases(self, value: ast.AST) -> set[str]:
        if not isinstance(value, ast.Name):
            return set()
        if value.id in self.module_callables:
            return {value.id}
        return self.callable_aliases.get(value.id, set())

    def _analyze_call(self, node: ast.Call, environment: PathEnvironment) -> None:
        if isinstance(node.func, ast.Name) and node.func.id == "open" and node.args:
            self.facts.reads_private_repo_data |= _is_protected_repo_path(
                node.args[0], environment
            )
        elif isinstance(node.func, ast.Attribute):
            if (
                node.func.attr == "open"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "builtins"
                and node.args
            ):
                self.facts.reads_private_repo_data |= _is_protected_repo_path(
                    node.args[0], environment
                )
            elif node.func.attr in READ_METHODS:
                self.facts.reads_private_repo_data |= _is_protected_repo_path(
                    node.func.value, environment
                )
        if isinstance(node.func, ast.Name):
            if node.func.id in self.module_callables:
                self.facts.calls.add(node.func.id)
            elif node.func.id in self.callable_aliases:
                self.facts.calls.update(self.callable_aliases[node.func.id])

    def _analyze_expression(self, node: ast.AST, environment: PathEnvironment) -> None:
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                self._analyze_call(child, environment)

    def _analyze_statements(
        self, statements: list[ast.stmt], environment: PathEnvironment
    ) -> PathEnvironment:
        current = _copy_path_environment(environment)
        for statement in statements:
            if isinstance(statement, ast.Assign):
                self._analyze_expression(statement.value, current)
                self._bind(current, statement.targets, statement.value)
            elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
                self._analyze_expression(statement.value, current)
                self._bind(current, [statement.target], statement.value)
            elif isinstance(statement, ast.If):
                self._analyze_expression(statement.test, current)
                current = _merge_path_environments(
                    self._analyze_statements(statement.body, current),
                    self._analyze_statements(statement.orelse, current),
                )
            elif isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
                if isinstance(statement, (ast.For, ast.AsyncFor)):
                    self._analyze_expression(statement.iter, current)
                elif isinstance(statement, ast.While):
                    self._analyze_expression(statement.test, current)
                body_environment = _copy_path_environment(current)
                if isinstance(statement, (ast.For, ast.AsyncFor)):
                    self._bind(body_environment, [statement.target], ast.Constant(None))
                current = _merge_path_environments(
                    current,
                    self._analyze_statements(statement.body, body_environment),
                    self._analyze_statements(statement.orelse, current),
                )
            elif isinstance(statement, ast.Try):
                alternatives = [self._analyze_statements(statement.body, current)]
                alternatives.extend(
                    self._analyze_statements(handler.body, current)
                    for handler in statement.handlers
                )
                alternatives.append(self._analyze_statements(statement.orelse, current))
                current = self._analyze_statements(
                    statement.finalbody, _merge_path_environments(*alternatives)
                )
            elif isinstance(statement, (ast.With, ast.AsyncWith)):
                for item in statement.items:
                    self._analyze_expression(item.context_expr, current)
                current = self._analyze_statements(statement.body, current)
            elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._analyze_statements(statement.body, current)
            else:
                self._analyze_expression(statement, current)
        return current


def _test_function_reads_private_repo_data(node: ast.AST) -> bool:
    assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    return _CallableAnalyzer(set()).analyze(node).reads_private_repo_data


def _is_fixture(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in node.decorator_list:
        value = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(value, ast.Attribute) and value.attr == "fixture":
            return True
    return False


def _is_marked_private_repo_data(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    decorators = list(getattr(node, "decorator_list", []))
    ancestor = parents[node]
    while isinstance(ancestor, ast.ClassDef):
        decorators.extend(ancestor.decorator_list)
        ancestor = parents[ancestor]
    return any(
        isinstance(decorator.func if isinstance(decorator, ast.Call) else decorator, ast.Attribute)
        and (decorator.func if isinstance(decorator, ast.Call) else decorator).attr
        == PRIVATE_REPO_DATA_MARKER
        for decorator in decorators
    )


def _test_nodes(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    tests: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            tests.append(node)
        elif isinstance(node, ast.ClassDef):
            tests.extend(
                child
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and child.name.startswith("test_")
            )
    return tests


def _node_id(node: ast.AST, relative_path: str, parents: dict[ast.AST, ast.AST]) -> str:
    classes: list[str] = []
    ancestor = parents[node]
    while isinstance(ancestor, ast.ClassDef):
        classes.append(ancestor.name)
        ancestor = parents[ancestor]
    return "::".join([relative_path, *reversed(classes), node.name])


def _private_repo_data_readers_from_source(
    source: str, relative_path: str
) -> dict[str, bool]:
    """Conservatively resolve same-module helper and fixture dependencies only.

    Module-external dynamic dispatch cannot be inferred from public AST; callers that
    may consume protected paths through such dispatch must declare the marker directly.
    """
    tree = ast.parse(source, filename=relative_path)
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    module_functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    fixtures = {
        name for name, node in module_functions.items() if _is_fixture(node)
    }
    facts = {
        name: _CallableAnalyzer(set(module_functions)).analyze(node)
        for name, node in module_functions.items()
    }
    for name, node in module_functions.items():
        if name in fixtures or name.startswith("test_"):
            facts[name].calls.update(
                argument.arg for argument in node.args.args if argument.arg in fixtures
            )

    def consumes_private_repo_data(name: str, seen: set[str]) -> bool:
        if name in seen:
            return True
        fact = facts.get(name)
        if fact is None:
            return True
        return fact.reads_private_repo_data or any(
            consumes_private_repo_data(called, seen | {name}) for called in fact.calls
        )

    readers: dict[str, bool] = {}
    for node in _test_nodes(tree):
        if node in module_functions.values():
            name = node.name
            consumes_private = consumes_private_repo_data(name, set())
        else:
            fact = _CallableAnalyzer(set(module_functions)).analyze(node)
            fact.calls.update(
                argument.arg for argument in node.args.args if argument.arg in fixtures
            )
            facts["__test_method__"] = fact
            consumes_private = consumes_private_repo_data("__test_method__", set())
        if consumes_private:
            readers[_node_id(node, relative_path, parents)] = _is_marked_private_repo_data(
                node, parents
            )
    return readers


def _private_repo_data_readers() -> dict[str, bool]:
    readers: dict[str, bool] = {}
    for test_path in TESTS.rglob("*.py"):
        readers.update(
            _private_repo_data_readers_from_source(
                _read(test_path), test_path.relative_to(ROOT).as_posix()
            )
        )
    return readers


def _fixture_test_function(body: str) -> ast.FunctionDef:
    source = "def test_reader(tmp_path):\n" + "\n".join(
        f"    {line}" for line in body.splitlines()
    )
    function = ast.parse(source).body[0]
    assert isinstance(function, ast.FunctionDef)
    return function


def _synthetic_private_reader_markers(source: str) -> dict[str, bool]:
    return _private_repo_data_readers_from_source(source, "tests/test_synthetic_private.py")


def test_module_analysis_marks_an_unmarked_private_fixture_consumer() -> None:
    readers = _synthetic_private_reader_markers(
        """
import pytest
from pathlib import Path

@pytest.fixture
def private_fixture():
    root = Path(__file__).parents[1] / "research" / "defects" / "real_corpus_v1"
    return (root / "selected_candidates.private.json").read_text()

def test_fixture_consumer(private_fixture):
    assert private_fixture
"""
    )

    assert readers == {"tests/test_synthetic_private.py::test_fixture_consumer": False}


def test_module_analysis_marks_an_unmarked_private_helper_caller() -> None:
    readers = _synthetic_private_reader_markers(
        """
from pathlib import Path

def private_helper():
    root = Path(__file__).parents[1] / "research" / "defects" / "real_corpus_v1"
    return (root / "selected_candidates.private.json").read_bytes()

def test_helper_consumer():
    assert private_helper()
"""
    )

    assert readers == {"tests/test_synthetic_private.py::test_helper_consumer": False}


def test_module_analysis_marks_an_unmarked_private_helper_alias_caller() -> None:
    readers = _synthetic_private_reader_markers(
        """
from pathlib import Path

def private_helper():
    root = Path(__file__).parents[1] / "research" / "defects" / "real_corpus_v1"
    return (root / "selected_candidates.private.json").read_bytes()

def test_helper_alias_consumer():
    callback = private_helper
    assert callback()
"""
    )

    assert readers == {"tests/test_synthetic_private.py::test_helper_alias_consumer": False}


def test_module_analysis_ignores_an_uncalled_private_helper_reference() -> None:
    readers = _synthetic_private_reader_markers(
        """
from pathlib import Path

def private_helper():
    root = Path(__file__).parents[1] / "research" / "defects" / "real_corpus_v1"
    return (root / "selected_candidates.private.json").read_bytes()

def test_helper_reference():
    callback = private_helper
    assert callback is private_helper
"""
    )

    assert readers == {}


def test_module_analysis_ignores_an_unknown_call_that_is_not_a_read_sink() -> None:
    readers = _synthetic_private_reader_markers(
        """
from pathlib import Path

def test_unknown_call():
    root = Path(__file__).parents[1] / "research" / "defects" / "real_corpus_v1"
    private_path = root / "selected_candidates.private.json"
    some_unknown(private_path)
"""
    )

    assert readers == {}


def test_module_analysis_propagates_private_fixture_through_another_fixture() -> None:
    readers = _synthetic_private_reader_markers(
        """
import pytest
from pathlib import Path

@pytest.fixture
def private_base_fixture():
    root = Path(__file__).parents[1] / "research" / "defects" / "real_corpus_v1"
    return (root / "selected_candidates.private.json").read_text()

@pytest.fixture
def intermediate_fixture(private_base_fixture):
    return private_base_fixture

def test_unmarked_fixture_chain(intermediate_fixture):
    assert intermediate_fixture

@pytest.mark.private_repo_data
def test_marked_fixture_chain(intermediate_fixture):
    assert intermediate_fixture
"""
    )

    assert readers == {
        "tests/test_synthetic_private.py::test_unmarked_fixture_chain": False,
        "tests/test_synthetic_private.py::test_marked_fixture_chain": True,
    }


def test_module_analysis_marks_an_unmarked_nested_private_reader() -> None:
    readers = _synthetic_private_reader_markers(
        """
from pathlib import Path

def test_nested_consumer():
    def private_reader():
        root = Path(__file__).parents[1] / "research" / "defects" / "real_corpus_v1"
        return (root / "selected_candidates.private.json").read_text()
    assert private_reader()
"""
    )

    assert readers == {"tests/test_synthetic_private.py::test_nested_consumer": False}


def test_module_analysis_unions_conditional_private_path_assignments() -> None:
    readers = _synthetic_private_reader_markers(
        """
from pathlib import Path

def test_conditional_consumer(tmp_path, use_private):
    root = Path(__file__).parents[1] / "research" / "defects" / "real_corpus_v1"
    if use_private:
        candidate = root / "selected_candidates.private.json"
    else:
        candidate = tmp_path / "scratch.json"
    return candidate.read_text()
"""
    )

    assert readers == {"tests/test_synthetic_private.py::test_conditional_consumer": False}


def test_module_analysis_unions_loop_and_try_private_path_assignments() -> None:
    readers = _synthetic_private_reader_markers(
        """
from pathlib import Path

def test_loop_consumer(tmp_path, use_private):
    root = Path(__file__).parents[1] / "research" / "defects" / "real_corpus_v1"
    for selected in (use_private,):
        if selected:
            candidate = root / "selected_candidates.private.json"
        else:
            candidate = tmp_path / "scratch.json"
    return candidate.read_text()

def test_try_consumer(tmp_path):
    root = Path(__file__).parents[1] / "research" / "defects" / "real_corpus_v1"
    try:
        candidate = root / "selected_candidates.private.json"
    except OSError:
        candidate = tmp_path / "scratch.json"
    return candidate.read_text()
"""
    )

    assert readers == {
        "tests/test_synthetic_private.py::test_loop_consumer": False,
        "tests/test_synthetic_private.py::test_try_consumer": False,
    }


def test_module_analysis_accepts_marked_consumers_and_ignores_tmp_nonreaders() -> None:
    readers = _synthetic_private_reader_markers(
        """
import pytest
from pathlib import Path

def private_helper():
    root = Path(__file__).parents[1] / "research" / "defects" / "real_corpus_v1"
    return (root / "selected_candidates.private.json").read_text()

@pytest.mark.private_repo_data
def test_marked_helper_consumer():
    assert private_helper()

def test_tmp_nonreader(tmp_path):
    candidate = tmp_path / "x.private.json"
    assert candidate.exists() is False
"""
    )

    assert readers == {"tests/test_synthetic_private.py::test_marked_helper_consumer": True}


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


def test_ast_read_sink_tracks_added_private_artifact_path_into_bare_open() -> None:
    function = _fixture_test_function(
        "\n".join(
            [
                'root = "research/defects/real_corpus_v1"',
                'private_path = root + "/x.private.json"',
                "payload = open(private_path).read()",
            ]
        )
    )

    assert _test_function_reads_private_repo_data(function)


def test_ast_read_sink_detects_bare_open_for_a_real_private_artifact() -> None:
    function = _fixture_test_function(
        "\n".join(
            [
                'root = Path(__file__).parents[1] / "research" / "defects" / "real_corpus_v1"',
                'payload = open(root / "selected_candidates.private.json").read()',
            ]
        )
    )

    assert _test_function_reads_private_repo_data(function)


def test_ast_read_sink_detects_path_open_for_a_real_private_artifact() -> None:
    function = _fixture_test_function(
        "\n".join(
            [
                'root = Path(__file__).parents[1] / "research" / "defects" / "real_corpus_v1"',
                'payload = (root / "selected_candidates.private.json").open().read()',
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
