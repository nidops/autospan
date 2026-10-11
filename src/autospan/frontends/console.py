"""Line console host. Domain commands are contributed by plugins."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from autospan._core.evidence_store import LocalEvidenceStore
from autospan._core.host import HostBuild
from autospan._core.runtime import RuntimeSession
from autospan.api.commands import CommandShell, Completion


def script_lines(text: str) -> list[str]:
    """Command lines of a script: blank lines and ``#`` comments are skipped."""
    lines = (line.strip() for line in text.splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def _enable_line_editing(shell: CommandShell) -> Any:
    try:
        readline: Any = importlib.import_module("readline")
    except ImportError:  # not available on every platform
        return

    def completer(text: str, state: int) -> str | None:
        line = readline.get_line_buffer()
        completion = shell.completion(line, readline.get_endidx())
        options = []
        for choice in completion.choices:
            value, cursor = Completion(
                completion.start, completion.end, (choice,), completion.quote
            ).apply(line)
            options.append(value[readline.get_begidx() : cursor])
        return options[state] if state < len(options) else None

    readline.set_completer(completer)
    readline.set_completer_delims(" \t")
    readline.parse_and_bind("tab: complete")
    readline.set_auto_history(False)
    readline.clear_history()
    return readline


def _tui_available() -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    return importlib.util.find_spec("textual") is not None


def run_batch(shell: CommandShell, lines: Sequence[str], *, keep_going: bool = False) -> int:
    """Run lines non-interactively; stop at the first failure unless ``keep_going``."""
    result = 0
    for line in lines:
        print(f"autospan> {shell.label(line)}")
        outcome = shell.execute(line)
        result = shell.status
        if outcome.exit or outcome.status == 130 or (outcome.status and not keep_going):
            break
    return result


def run_console(
    *,
    host: HostBuild,
    evidence_root: Path,
    commands: Sequence[str] = (),
    script: Path | None = None,
    keep_going: bool = False,
    plain: bool = False,
    enabled_gates: frozenset[str] = frozenset(),
) -> int:
    batch = list(commands)
    if script is not None:
        batch.extend(script_lines(script.read_text(encoding="utf-8")))
    evidence = LocalEvidenceStore(evidence_root)
    result = 0
    error: BaseException | None = None
    try:
        session = RuntimeSession(
            plan=host.plan,
            factories=host.factories,
            evidence=evidence,
            enabled_gates=enabled_gates,
        )
        with session as runtime:
            shell = CommandShell(runtime, host.commands)
            if batch or script is not None:
                result = run_batch(shell, batch, keep_going=keep_going)
            elif not plain and _tui_available():
                from autospan.frontends.tui.app import ConsoleApp

                app = ConsoleApp(shell, runtime.names())
                try:
                    result = int(app.run() or 0)
                finally:
                    app.wait_for_command()
                result = app.status
                if result:
                    print(app.failure_diagnostic or f"Console session failed (status {result})")
                    print(f"Evidence: {shell.evidence_root}")
            else:
                readline = _enable_line_editing(shell)
                print("AutoSpan console. 'help' lists commands, Tab completes.")
                while True:
                    try:
                        line = input("autospan> ")
                    except (EOFError, KeyboardInterrupt):
                        print()
                        break
                    outcome = shell.execute(line)
                    result = shell.status
                    if readline is not None and line.strip():
                        readline.add_history(shell.label(line))
                        while readline.get_current_history_length() > 100:
                            readline.remove_history_item(0)
                    if outcome.exit:
                        break
            if not result:
                result = shell.status
            session.set_outcome("passed" if result == 0 else "failed")
        return result
    except BaseException as exc:
        error = exc
        result = max(result, 1)
        raise
    finally:
        try:
            evidence.close(
                outcome="passed" if result == 0 else "failed",
                metadata={"frontend": "console"},
            )
        except BaseException as reporting_error:
            if error is None:
                raise
            error.add_note(f"Evidence finalization failed: {reporting_error!r}")
