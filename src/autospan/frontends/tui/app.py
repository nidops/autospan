"""Textual console: one worker, bounded output and orderly runtime shutdown."""

from __future__ import annotations

import contextvars
import queue
import threading
from collections import deque
from functools import partial
from pathlib import Path
from typing import cast

from autospan.api import CommandOutcome, CommandShell
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Input, RichLog, Static

from autospan.frontends.tui.editing import History


class PromptInput(Input):
    """Cursor-aware completion and bounded, argument-free command history."""

    BINDINGS = [
        Binding("tab", "complete", "complete", show=False, priority=True),
        Binding("up", "history_previous", "history", show=False),
        Binding("down", "history_next", "history", show=False),
    ]

    def __init__(self, shell: CommandShell, history: History) -> None:
        super().__init__(placeholder="command (Tab completes, Ctrl+P picks)", id="prompt")
        self._shell = shell
        self._history = history

    def _set(self, value: str) -> None:
        self.value = value
        self.cursor_position = len(value)

    def action_complete(self) -> None:
        completion = self._shell.completion(self.value, self.cursor_position)
        value, cursor = completion.apply(self.value)
        if completion.choices:
            self.value = value
            self.cursor_position = cursor
        app = cast(ConsoleApp, self.app)
        if len(completion.choices) > 1:
            app.record("  ".join(completion.choices))
        if completion.diagnostic:
            app.record(completion.diagnostic)

    def action_history_previous(self) -> None:
        self._set(self._history.previous(self.value))

    def action_history_next(self) -> None:
        self._set(self._history.next(self.value))


class ConsoleCommands(Provider):
    """Native fuzzy palette, deriving commands from the existing contributions."""

    async def discover(self) -> Hits:
        app = cast(ConsoleApp, self.app)
        for command in app.shell.commands:
            label = " ".join(command.path)
            yield DiscoveryHit(label, partial(app.select_command, label), help=command.help)

    async def search(self, query: str) -> Hits:
        app = cast(ConsoleApp, self.app)
        matcher = self.matcher(query)
        for command in app.shell.commands:
            label = " ".join(command.path)
            score = matcher.match(label)
            if score:
                yield Hit(
                    score,
                    matcher.highlight(label),
                    partial(app.select_command, label),
                    text=label,
                    help=command.help,
                )


class ConsoleApp(App[int]):
    """Keep the UI responsive while one command owns the live runtime."""

    TITLE = "AutoSpan console"
    COMMANDS = {ConsoleCommands}
    CSS = """
    #side { width: 30; padding: 1; border-right: solid $primary-darken-2; }
    #log { height: 1fr; padding: 0 1; }
    #search { display: none; }
    """
    BINDINGS = [
        Binding("ctrl+c", "cancel_command", "stop", priority=True),
        Binding("ctrl+q", "quit_console", "quit", priority=True),
        Binding("ctrl+p", "command_palette", "commands", priority=True),
        Binding("ctrl+f", "search_log", "search log", priority=True),
        Binding("ctrl+y", "copy_log", "copy log", priority=True),
        Binding("ctrl+s", "export_log", "export log", priority=True),
    ]

    def __init__(self, shell: CommandShell, components: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.shell = shell
        self._components = components
        self._history = History()
        self._status = 0
        self._last_status: int | None = None
        self._active_label = ""
        self._thread: threading.Thread | None = None
        self._output: queue.Queue[str] = queue.Queue(maxsize=128)
        self._completed = threading.Event()
        self._cancel_request = threading.Event()
        self._outcome: CommandOutcome | None = None
        self._quit_requested = False
        self._log_text: deque[str] = deque(maxlen=1000)
        self._filter = ""
        self._status_text = ""
        self._failure_diagnostic = ""

    @property
    def status(self) -> int:
        return self._status or self.shell.status

    @property
    def failure_diagnostic(self) -> str:
        """Retain the last failure even if UI shutdown discards queued display output."""
        return self._failure_diagnostic

    @property
    def operation_state(self) -> str:
        if self._thread is not None:
            return "stopping" if self._cancel_request.is_set() else "running"
        return "failed" if self.status else "idle"

    @property
    def retained_log(self) -> str:
        """Plain text for explicit clipboard/export, bounded to 1000 retained lines."""
        return "\n".join(self._log_text)

    def compose(self) -> ComposeResult:
        yield Header()
        self._side = Static("", id="side", markup=False)
        self._log_view = RichLog(id="log", wrap=True, markup=False, highlight=False, max_lines=1000)
        self._search = Input(
            placeholder="Search retained log; Enter filters, empty restores", id="search"
        )
        self._prompt = PromptInput(self.shell, self._history)
        with Horizontal():
            yield self._side
            yield self._log_view
        yield self._search
        yield self._prompt
        yield Footer()

    def on_mount(self) -> None:
        self.record("help / Tab / Ctrl+P commands; Ctrl+C stops; Ctrl+Q quits.")
        self._prompt.focus()
        self.set_interval(0.02, self._drain_output)
        self._update_status()

    def record(self, text: str) -> None:
        """Record/render on the UI thread only; limit individual retained lines too."""
        log = self._log_view
        for line in text.splitlines():
            # Built-ins and completion suggestions also obey the retention bound.
            for start in range(0, max(1, len(line)), 4096):
                chunk = line[start : start + 4096]
                self._log_text.append(chunk)
                if not self._filter or self._filter in chunk.casefold():
                    log.write(chunk)

    def _enqueue_output(self, text: str) -> None:
        # Blocking put supplies backpressure. Both normal timers and emergency
        # shutdown drain the queue so a producer cannot outlive runtime closure.
        for start in range(0, len(text), 2048):
            self._output.put(text[start : start + 2048])

    def select_command(self, label: str) -> None:
        prompt = self._prompt
        prompt._set(label + " ")
        prompt.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if event.input.id == "search":
            self._filter = event.value.casefold()
            log = self._log_view
            log.clear()
            for line in self._log_text:
                if not self._filter or self._filter in line.casefold():
                    log.write(line)
            self._prompt.focus()
            return
        line = event.value.strip()
        event.input.value = ""
        if not line:
            return
        if self._thread is not None:
            self.record("command running; wait or press Ctrl+C to request cancellation")
            return
        self._active_label = self.shell.label(line)
        self._history.add(self._active_label)
        self.record(f"> {self._active_label}")
        self._completed.clear()
        self._cancel_request = threading.Event()
        self._outcome = None
        context = contextvars.copy_context()

        def execute() -> None:
            try:
                self._outcome = context.run(
                    self.shell.execute,
                    line,
                    output=self._enqueue_output,
                    cancellation=self._cancel_request,
                )
            except BaseException as exc:
                diagnostic = f"console worker failed: {type(exc).__name__}"
                self._enqueue_output(diagnostic + "\n")
                self._outcome = CommandOutcome(2, diagnostic=diagnostic, error=exc)
            finally:
                self._completed.set()

        self._thread = threading.Thread(target=execute, name="autospan-console-command")
        self._thread.start()
        self._update_status()

    def _drain_output(self) -> None:
        if not self.is_running:
            return
        for _ in range(8):
            try:
                self.record(self._output.get_nowait())
            except queue.Empty:
                break
        if self._thread is not None and self._completed.is_set() and self._output.empty():
            self._thread.join()
            self._thread = None
            self._accept_outcome()
            if self._quit_requested:
                self.exit(self.status, return_code=self.status)
                return
        self._update_status()

    def _accept_outcome(self) -> None:
        outcome = self._outcome
        if outcome is None:
            return
        self._last_status = outcome.status
        if outcome.status:
            self._status = outcome.status if not self._status else max(self._status, outcome.status)
            self._failure_diagnostic = outcome.diagnostic
        self._quit_requested |= outcome.exit
        self._active_label = ""

    def _update_status(self) -> None:
        parts = [
            f"State: {self.operation_state}",
            f"Operation: {self._active_label or '-'}",
            f"Last status: {self._last_status if self._last_status is not None else '-'}",
            "",
            "Components",
            *(f"  {name}" for name in self._components),
            "",
            "Evidence",
            self.shell.evidence_root,
        ]
        text = "\n".join(parts)
        if text != self._status_text:
            self._side.update(text)
            self._status_text = text

    def action_cancel_command(self) -> None:
        if self._thread is not None and not self._completed.is_set():
            self._cancel_request.set()
            self.record("Stopping requested; waiting for the operation to stop or finish.")
            self._update_status()

    def action_quit_console(self) -> None:
        self._quit_requested = True
        if self._thread is None:
            self.exit(self.status, return_code=self.status)
        else:
            self.action_cancel_command()

    async def action_quit(self) -> None:
        self.action_quit_console()

    def action_search_log(self) -> None:
        search = self._search
        search.display = True
        search.focus()

    def action_copy_log(self) -> None:
        self.copy_to_clipboard(self.retained_log)
        self.record("Retained log sent to the terminal clipboard; Ctrl+S exports a UTF-8 file.")

    def action_export_log(self) -> None:
        root = Path(self.shell.evidence_root)
        try:
            if not root.is_dir():
                raise OSError("no local evidence directory")
            path = root / "console.log"
            path.write_text(self.retained_log + "\n", encoding="utf-8")
        except OSError as exc:
            self.record(f"Log export failed: {exc}")
        else:
            self.record(f"Log exported: {path}")

    def wait_for_command(self) -> None:
        """Emergency exit must drain/backpressure and join before runtime teardown.

        Drivers that ignore cancellation are allowed to finish. There is no
        timeout that abandons a thread still holding hardware resources.
        """
        thread = self._thread
        if thread is None:
            return
        self._cancel_request.set()
        while thread.is_alive():
            try:
                self._output.get(timeout=0.05)
            except queue.Empty:
                pass
        thread.join()
        self._thread = None
        self._accept_outcome()

    def on_unmount(self) -> None:
        self.wait_for_command()
