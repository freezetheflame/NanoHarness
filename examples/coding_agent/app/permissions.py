"""Permission gate: 4-step pipeline for tool call authorization.

    tool_call
      │
      ▼
  1. deny rules     → hit → REJECT
      │
      ▼
  2. mode check     → yolo: pass everything / auto: deny unknowns
      │
      ▼
  3. allow rules    → hit → PASS
      │
      ▼
  4. ask user       → user confirms or rejects

Modes:
    interactive  — unlisted tools ask user (REPL default)
    auto         — unlisted tools auto-deny (single-shot default)
    yolo         — everything not denied passes (no user prompts)

Select a mode with the CLI (--perm-mode / --auto / --yolo / --interactive)
or the NANOHARNESS_PERM_MODE environment variable. An explicit argument
wins over the environment variable. With neither set, build_permissions()
stays interactive; the CLI picks auto for a one-shot task so a closed
stdin cannot block on input().
"""

import fnmatch
import os
from abc import ABC, abstractmethod
from collections.abc import Mapping
from enum import Enum
from typing import Callable, Dict, List, Optional


# ── App-layer types (engine duck-types these) ──


class PermissionLevel(str, Enum):
    DENY = "deny"
    CONFIRM = "confirm"
    ALLOW = "allow"


class BasePermissionManager(ABC):
    """App-layer ABC — engine duck-types enforce()."""

    @abstractmethod
    def check(self, tool_name: str, args: Dict) -> PermissionLevel: ...

    @abstractmethod
    def enforce(self, tool_name: str, args: Dict) -> Optional[str]: ...


class GateMode(str, Enum):
    AUTO = "auto"
    INTERACTIVE = "interactive"
    YOLO = "yolo"


# Read by build_permissions() when no mode argument is passed.
PERM_MODE_ENV = "NANOHARNESS_PERM_MODE"

_MODE_BY_NAME = {mode.value: mode for mode in GateMode}


def parse_gate_mode(value: str | GateMode) -> GateMode:
    """Parse a permission mode name. Accepts auto, interactive, or yolo."""
    if isinstance(value, GateMode):
        return value
    key = str(value).strip().lower()
    try:
        return _MODE_BY_NAME[key]
    except KeyError:
        allowed = ", ".join(mode.value for mode in GateMode)
        raise ValueError(
            f"Unknown permission mode {value!r}. Expected one of: {allowed}."
        ) from None


def mode_from_env(environ: Mapping[str, str] | None = None) -> GateMode | None:
    """Return NANOHARNESS_PERM_MODE, or None when it is unset or blank."""
    source = os.environ if environ is None else environ
    raw = source.get(PERM_MODE_ENV, "")
    if raw is None or not str(raw).strip():
        return None
    return parse_gate_mode(raw)


def resolve_permission_mode(
    cli_mode: str | None = None,
    *,
    single_shot: bool = False,
    environ: Mapping[str, str] | None = None,
) -> GateMode:
    """Choose the startup permission mode.

    Priority: explicit CLI value, then NANOHARNESS_PERM_MODE, then a
    context default. Single-shot tasks default to AUTO so they never
    call input(); the REPL defaults to INTERACTIVE.
    """
    if cli_mode:
        return parse_gate_mode(cli_mode)
    env_mode = mode_from_env(environ)
    if env_mode is not None:
        return env_mode
    if single_shot:
        return GateMode.AUTO
    return GateMode.INTERACTIVE


class PermissionGate(BasePermissionManager):
    """4-step permission pipeline: deny → mode → allow → ask.

    Separates deny rules and allow rules into distinct lists,
    with a runtime mode that controls the fallback behavior.
    """

    def __init__(
        self,
        mode: GateMode = GateMode.INTERACTIVE,
        approval_callback: Optional[Callable[[str, Dict], bool]] = None,
    ):
        self._deny_rules: List[str] = []   # glob patterns
        self._allow_rules: List[str] = []  # glob patterns
        self._mode = mode
        self._approval_callback = approval_callback

    # ── Configuration ──

    def deny(self, pattern: str):
        """Add a deny rule (glob pattern: 'git_reset', 'git_*', '*')."""
        self._deny_rules.append(pattern)

    def allow(self, pattern: str):
        """Add an allow rule (glob pattern)."""
        self._allow_rules.append(pattern)

    def set_mode(self, mode: GateMode):
        """Switch runtime mode: auto / interactive / yolo."""
        self._mode = mode

    @property
    def mode(self) -> GateMode:
        return self._mode

    # ── 4-step pipeline ──

    def check(self, tool_name: str, args: Dict) -> PermissionLevel:
        """Run the 4-step pipeline. Returns the decided level."""

        # Step 1: deny rules — hit → REJECT
        if self._matches(tool_name, self._deny_rules):
            return PermissionLevel.DENY

        # Step 2: mode check — yolo bypasses everything else
        if self._mode == GateMode.YOLO:
            return PermissionLevel.ALLOW

        # Step 3: allow rules — hit → PASS
        if self._matches(tool_name, self._allow_rules):
            return PermissionLevel.ALLOW

        # Step 4: fallback — depends on mode
        if self._mode == GateMode.INTERACTIVE:
            return PermissionLevel.CONFIRM  # ask user

        # auto mode: unlisted → deny
        return PermissionLevel.DENY

    def enforce(self, tool_name: str, args: Dict) -> Optional[str]:
        """Check permissions and handle approval.

        Returns None if allowed, or an error message string if blocked.
        """
        level = self.check(tool_name, args)

        if level == PermissionLevel.DENY:
            return f"Permission denied for tool '{tool_name}'"

        if level == PermissionLevel.CONFIRM:
            try:
                if self._approval_callback:
                    approved = self._approval_callback(tool_name, args)
                else:
                    approved = _default_terminal_approve(tool_name, args)
            except EOFError:
                # Closed or exhausted stdin (single-shot, CI, piped input).
                # Deny the call instead of crashing the run.
                print()
                print(
                    "[Permission] No interactive input available; denying "
                    f"'{tool_name}'. Use --auto, --yolo, or {PERM_MODE_ENV}."
                )
                return (
                    f"Tool '{tool_name}' not approved: no interactive stdin "
                    "(permission mode is interactive)"
                )
            if not approved:
                return f"Tool '{tool_name}' not approved by user"

        return None  # Allowed

    # ── Helpers ──

    @staticmethod
    def _matches(tool_name: str, patterns: List[str]) -> bool:
        return any(fnmatch.fnmatch(tool_name, p) for p in patterns)

    def reset(self):
        self._deny_rules.clear()
        self._allow_rules.clear()
        self._mode = GateMode.INTERACTIVE


def _default_terminal_approve(tool_name: str, args: Dict) -> bool:
    """Fallback approval via terminal input(). Override via approval_callback.

    Raises EOFError when stdin is closed. PermissionGate.enforce catches that
    and denies the tool instead of letting the process crash.
    """
    msg = f"[Permission] Tool '{tool_name}' requests approval. Args: {args}\nAllow? [y/N] "
    print(msg, end="", flush=True)
    response = input().strip().lower()
    return response == "y"


# ── App-level policy ──


def build_permissions(mode: GateMode | str | None = None) -> PermissionGate:
    """Build the coding agent permission gate.

    Policy:
        Deny:  destructive git ops
        Allow: read-only tools, memory, skills, subagent
        Ask:   everything else (write ops, shell, git mutations)

    ``mode`` selects the fallback for tools that are neither denied nor
    allowed. When it is omitted, NANOHARNESS_PERM_MODE is used if set,
    otherwise interactive. An explicit mode overrides the environment.
    """
    if mode is None:
        resolved = mode_from_env() or GateMode.INTERACTIVE
    else:
        resolved = parse_gate_mode(mode)
    gate = PermissionGate(mode=resolved)

    # Step 1: deny — always blocked
    gate.deny("git_reset")
    gate.deny("git_revert")

    # Step 3: allow — always pass
    gate.allow("file_read")
    gate.allow("file_list")
    gate.allow("file_find")
    gate.allow("search_code")
    gate.allow("list_files")
    gate.allow("git_status")
    gate.allow("git_diff")
    gate.allow("git_log")
    gate.allow("git_show")
    gate.allow("git_branch_list")
    gate.allow("git_remote_list")
    gate.allow("git_stash_list")
    gate.allow("sys_info")
    gate.allow("save_memory")
    gate.allow("recall_memory")
    gate.allow("list_memories")
    gate.allow("skill")
    gate.allow("task")

    # MCP tools — allow by server name prefix
    gate.allow("mcp__filesystem__*")

    # Step 4: everything else → ask user
    # file_write, file_edit, shell_exec, git_commit, git_push, etc.

    return gate
