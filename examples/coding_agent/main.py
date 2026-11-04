#!/usr/bin/env python3
"""Coding Agent — a terminal-based coding assistant.

Usage:
    export DEEPSEEK_API_KEY="sk-..."
    cd examples/coding_agent
    python main.py

    # Or with a single task (runs once, prints report, exits).
    # Single-shot defaults to permission mode "auto" (no stdin prompt).
    python main.py "Add a docstring to engine.py"
    python main.py --yolo "Add a docstring to engine.py"
    NANOHARNESS_PERM_MODE=interactive python main.py "Add a docstring to engine.py"
"""

import argparse
import sys
import os

# Ensure example directory is on sys.path so that both
# nanoharness/ (symlink) and app/ are importable.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from app.builder import build_coding_engine
from app.permissions import PERM_MODE_ENV, GateMode, resolve_permission_mode
from app.ui import BANNER, read_input

_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_RESET = "\033[0m"


def run_repl(engine):
    """Interactive REPL loop — keeps running until /quit."""
    print(BANNER)

    while True:
        query = read_input()

        if query is None:
            print("Bye!")
            break

        if query == "":
            continue

        if query == "/clear":
            engine.context.reset()
            print("  Context cleared.\n")
            continue

        try:
            report = engine.run(query)

            # Post-run: verify goal completion
            _print_goal_verification(engine, query, report)

            # Post-run: optimize context for next round (compress → summarize)
            engine.context.compress_old()
            engine.context.summarize_if_needed()

        except KeyboardInterrupt:
            print(f"\n  Interrupted.")
        except Exception as e:
            print(f"\n  Error: {e}")


def _print_goal_verification(engine, query, report):
    """Print goal verification from the evaluator's result."""
    evaluation = report.get("summary", {}).get("evaluation", {})
    if not evaluation:
        return

    achieved = evaluation.get("achieved", False)
    explanation = evaluation.get("explanation", "")

    if achieved:
        print(f"  {_GREEN}Goal achieved:{_RESET} {_DIM}{explanation.split(':', 1)[-1].strip()}{_RESET}")
    else:
        print(f"  {_RED}Goal not achieved:{_RESET} {_DIM}{explanation.split(':', 1)[-1].strip()}{_RESET}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI flags and an optional single-shot task."""
    parser = argparse.ArgumentParser(
        prog="python main.py",
        description=(
            "Terminal coding agent. With no task, starts an interactive REPL. "
            "A task argument runs once and exits."
        ),
        epilog=(
            "Permission mode, highest priority first:\n"
            "  --yolo / --auto / --interactive or --perm-mode,\n"
            f"  then {PERM_MODE_ENV},\n"
            "  then auto for a single-shot task and interactive for the REPL.\n"
            "\n"
            "examples:\n"
            "  python main.py\n"
            '  python main.py --yolo "Add a docstring to engine.py"\n'
            f'  {PERM_MODE_ENV}=auto python main.py "List the project tools"'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--perm-mode",
        dest="perm_mode",
        choices=[m.value for m in GateMode],
        help=(
            "Permission mode. interactive prompts on stdin; "
            "auto denies tools that are not allow-listed; "
            "yolo allows anything that is not explicitly denied."
        ),
    )
    mode.add_argument(
        "--yolo",
        action="store_const",
        const=GateMode.YOLO.value,
        dest="perm_mode",
        help="Shortcut for --perm-mode yolo (no prompts; deny rules still apply).",
    )
    mode.add_argument(
        "--auto",
        action="store_const",
        const=GateMode.AUTO.value,
        dest="perm_mode",
        help="Shortcut for --perm-mode auto (no prompts; unlisted tools denied).",
    )
    mode.add_argument(
        "--interactive",
        action="store_const",
        const=GateMode.INTERACTIVE.value,
        dest="perm_mode",
        help="Shortcut for --perm-mode interactive (prompt for confirmation).",
    )
    parser.add_argument(
        "task",
        nargs="*",
        help="Single-shot task. Omit to start the REPL.",
    )
    return parser.parse_args(argv)


def _print_permission_mode(mode: GateMode, *, single_shot: bool):
    """Tell the operator which gate is active before any tool runs."""
    if mode == GateMode.AUTO:
        detail = (
            "unlisted tools (shell, writes, git mutations) are denied "
            "without prompting. Use --yolo to allow them, or --interactive to prompt."
        )
    elif mode == GateMode.YOLO:
        detail = "anything not explicitly denied runs without prompting."
    else:
        if not single_shot:
            return
        detail = "tools that need confirmation will prompt on stdin."
    print(f"{_DIM}Permission mode: {mode.value} — {detail}{_RESET}")


def main(argv: list[str] | None = None):
    args = parse_args(argv)
    single_shot = bool(args.task)
    try:
        mode = resolve_permission_mode(args.perm_mode, single_shot=single_shot)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)

    try:
        engine = build_coding_engine(permission_mode=mode)
    except KeyError:
        print("Error: DEEPSEEK_API_KEY environment variable not set.")
        print("  export DEEPSEEK_API_KEY=\"sk-...\"")
        sys.exit(1)

    _print_permission_mode(mode, single_shot=single_shot)

    if single_shot:
        query = " ".join(args.task)
        report = engine.run(query)
        _print_goal_verification(engine, query, report)
        _print_trajectory(report)
    else:
        # Interactive REPL
        run_repl(engine)


def _print_trajectory(report):
    """Print full trajectory (single-shot mode)."""
    print("\n===== Trajectory =====")
    for i, step in enumerate(report["trajectory"]):
        print(f"\n--- Step {i} [{step['status']}] ---")
        print(f"  Thought: {step['thought'][:300]}")
        if step["observation"]:
            print(f"  Observation: {step['observation'][:500]}")


if __name__ == "__main__":
    main()
