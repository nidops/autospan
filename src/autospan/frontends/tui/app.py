"""Textual console: one worker, bounded output and orderly runtime shutdown."""

from __future__ import annotations

import contextvars
import queue
import threading
import time
from collections import deque
from functools import partial
from pathlib import Path
from typing import cast

from autospan.api import CommandOutcome, CommandShell, Completion
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Input, OptionList, RichLog, Static

from autospan.frontends.tui.editing import History
from autospan.frontends.tui.streaming import LineAssembler


class PromptInput(Input):
    """Cursor-aware completion and bounded, argument-free command history."""

    BINDINGS = [
        Binding("tab", "complete", "complete", show=False, priority=True),
        Binding("shift+tab", "complete_previous", "previous choice", show=False, priority=True),
        Binding("up", "history_previous", "history", show=False),
        Binding("down", "history_next", "history", show=False),
    ]

    def __init__(self, shell: CommandShell, history: History) -> None:
        # Expected value/cursor, original input, token span/choices, selected index.
        self._completion_cycle: tuple[str, int, str, Completion, int] | None = None
        super().__init__(placeholder="command (Tab cycles, Ctrl+P picks)", id="prompt")
        self._shell = shell
        self._history = history

    def watch_value(self) -> None:
        self._completion_cycle = None

    def watch_cursor_position(self) -> None:
        # Older Textual versions use a reactive cursor instead of a selection.
        self._completion_cycle = None
        self._dismiss_stale_suggestions()

    def watch_selection(self) -> None:
        self._completion_cycle = None
        self._dismiss_stale_suggestions()

    def _dismiss_stale_suggestions(self) -> None:
        if not self.is_mounted:
            return
        app = cast(ConsoleApp, self.app)
        snapshot = app._suggestion_snapshot
        # Typing refreshes via Input.Changed; cursor-only movement invalidates
        # the token span, including movement by mouse or Ctrl+Left/Right.
        if snapshot is not None and self.value == snapshot[0]:
            if self.cursor_position != snapshot[1]:
                app.dismiss_suggestions()

    def on_blur(self) -> None:
        cast(ConsoleApp, self.app).dismiss_suggestions()

    def _set(self, value: str) -> None:
        self._completion_cycle = None
        self.value = value
        self.cursor_position = len(value)

    def action_complete(self) -> None:
        app = cast(ConsoleApp, self.app)
        if app.suggestions_open:
            app.accept_suggestion()
        else:
            self._complete(direction=1)

    def action_complete_previous(self) -> None:
        app = cast(ConsoleApp, self.app)
        if app.suggestions_open:
            app.move_suggestion(-1)
        else:
            self._complete(direction=-1)

    def _complete(self, *, direction: int) -> None:
        cycle = self._completion_cycle
        if cycle is not None:
            expected, position, original, candidates, selected = cycle
            if self.value == expected and self.cursor_position == position:
                if selected < 0:
                    index = 0 if direction > 0 else len(candidates.choices) - 1
                else:
                    index = (selected + direction) % len(candidates.choices)
                choice = Completion(
                    candidates.start,
                    candidates.end,
                    (candidates.choices[index],),
                    candidates.quote,
                )
                value, cursor = choice.apply(original)
                self.value = value
                self.cursor_position = cursor
                self._completion_cycle = (value, cursor, original, candidates, index)
                return

        # Any intervening edit or cursor movement starts a fresh completion.
        self._completion_cycle = None
        original = self.value
        completion = self._shell.completion(original, self.cursor_position)
        value, cursor = completion.apply(original)
        if completion.choices:
            self.value = value
            self.cursor_position = cursor
        app = cast(ConsoleApp, self.app)
        if len(completion.choices) > 1:
            app.record("  ".join(completion.choices) + " (Tab / Shift+Tab to cycle)")
            # A partial quoted prefix may swallow suffix tokens if parsed again.
            # Keep the original span and candidate snapshot for the whole cycle.
            self._completion_cycle = (self.value, self.cursor_position, original, completion, -1)
        if completion.diagnostic:
            app.record(completion.diagnostic)

    def action_history_previous(self) -> None:
        app = cast(ConsoleApp, self.app)
        if app.suggestions_open:
            app.move_suggestion(-1)
        else:
            self._set(self._history.previous(self.value))

    def action_history_next(self) -> None:
        app = cast(ConsoleApp, self.app)
        if app.suggestions_open:
            app.move_suggestion(1)
        else:
            self._set(self._history.next(self.value))


class SuggestionList(OptionList, can_focus=False):
    """Mouse-selectable choices that leave keyboard editing in the prompt."""


class ConsoleCommands(Provider):
    """Native fuzzy palette for built-ins and plugin-contributed command paths."""

    def _entries(self) -> tuple[tuple[str, str], ...]:
        app = cast(ConsoleApp, self.app)
        return (
            ("help", "show available commands"),
            ("components", "list live components"),
            ("evidence", "show evidence location"),
            ("exit", "leave the console"),
            ("quit", "leave the console"),
            *((" ".join(command.path), command.help) for command in app.shell.commands),
        )

    async def discover(self) -> Hits:
        app = cast(ConsoleApp, self.app)
        for label, help_text in self._entries():
            yield DiscoveryHit(label, partial(app.select_command, label), help=help_text)

    async def search(self, query: str) -> Hits:
        app = cast(ConsoleApp, self.app)
        matcher = self.matcher(query)
        for label, help_text in self._entries():
            score = matcher.match(label)
            if score:
                yield Hit(
                    score,
                    matcher.highlight(label),
                    partial(app.select_command, label),
                    text=label,
                    help=help_text,
                )


class ConsoleApp(App[int]):
    """Keep the UI responsive while one command owns the live runtime."""

    TITLE = "AutoSpan console"
    COMMANDS = {ConsoleCommands}
    CSS = """
    #side { width: 30; padding: 1; border-right: solid $primary-darken-2; }
    #log { height: 1fr; padding: 0 1; }
    #search, #suggestions, #suggestion-hint { display: none; }
    #suggestions { height: auto; max-height: 10; border: round $primary; }
    #suggestion-hint { padding: 0 1; color: $text-muted; }
    """
    BINDINGS = [
        Binding("ctrl+c", "cancel_command", "stop", priority=True),
        Binding("ctrl+q", "quit_console", "quit", priority=True),
        Binding("ctrl+p", "command_palette", "commands", priority=True),
        # Ctrl+P searches globally; Ctrl+Space asks what belongs at the cursor.
        Binding("ctrl+space", "browse_suggestions", "suggest", show=False, priority=True),
        Binding("ctrl+@", "browse_suggestions", "suggest", show=False, priority=True),
        Binding("ctrl+f", "search_log", "search log", priority=True),
        Binding("ctrl+y", "copy_log", "copy log", priority=True),
        Binding("ctrl+s", "export_log", "export log", priority=True),
        Binding("ctrl+b", "toggle_sidebar", "sidebar", priority=True),
        Binding("ctrl+g", "follow_log", "latest output", priority=True),
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
        self._stream = LineAssembler(max_line=4096)
        self._filter = ""
        self._status_text = ""
        self._failure_diagnostic = ""
        self._sidebar_override: bool | None = None
        self._started_at: float | None = None
        self._finished_at: float | None = None
        self._last_duration: float | None = None
        # (prompt value, cursor, completion input, completion snapshot)
        self._suggestion_snapshot: tuple[str, int, str, Completion] | None = None

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
        self._suggestion_hint = Static("", id="suggestion-hint", markup=False)
        self._suggestions = SuggestionList(id="suggestions", markup=False)
        with Horizontal():
            yield self._side
            yield self._log_view
        yield self._search
        yield self._suggestion_hint
        yield self._suggestions
        yield self._prompt
        yield Footer()

    def on_mount(self) -> None:
        self.record(
            "help / Tab completes / Ctrl+Space suggests / Ctrl+P finds commands / "
            "Ctrl+B toggles details / Ctrl+C stops / Ctrl+Q quits"
        )
        self._prompt.focus()
        self.set_interval(0.02, self._drain_output)
        self._refresh_sidebar()
        self._update_status()

    def on_resize(self, event: events.Resize) -> None:
        # Keep the command log readable on an 80-column terminal. The operator
        # can explicitly override the automatic compact layout with Ctrl+B.
        if hasattr(self, "_side"):
            self._refresh_sidebar(width=event.size.width)

    def _refresh_sidebar(self, *, width: int | None = None) -> None:
        visible = self._sidebar_override
        if width is None:
            width = self.size.width
        self._side.display = width >= 90 if visible is None else visible

    def _record_line(self, line: str) -> None:
        """Retain and render one display line, including deliberate blank lines."""
        self._log_text.append(line)
        if not self._filter or self._filter in line.casefold():
            self._log_view.write(line)

    def record(self, text: str) -> None:
        """Record UI messages; command output uses the framing path below."""
        for line in text.splitlines():
            for start in range(0, max(1, len(line)), 4096):
                self._record_line(line[start : start + 4096])

    def _enqueue_output(self, text: str) -> None:
        # Blocking put supplies backpressure. Both normal timers and emergency
        # shutdown drain the queue so a producer cannot outlive runtime closure.
        for start in range(0, len(text), 2048):
            self._output.put(text[start : start + 2048])

    @property
    def suggestions_open(self) -> bool:
        return self._suggestion_snapshot is not None

    def dismiss_suggestions(self) -> None:
        self._suggestion_snapshot = None
        if hasattr(self, "_suggestions"):
            self._suggestions.display = False
            self._suggestion_hint.display = False

    def action_browse_suggestions(self) -> None:
        if (
            self._thread is not None
            or self.screen is not self._prompt.screen
            or not self._prompt.has_focus
        ):
            return
        self._refresh_suggestions()

    def action_command_palette(self) -> None:
        self.dismiss_suggestions()
        super().action_command_palette()

    def _refresh_suggestions(self) -> None:
        """Request shell-owned completions, never execute or touch hardware."""
        prompt = self._prompt
        original, cursor = prompt.value, prompt.cursor_position
        source = original
        # Asking after an exact path (`lab`, `lab read`) means 'show children'.
        # Insert a *virtual* separator so unfinished tokens still work normally.
        prefix = tuple(original[:cursor].split())
        if (
            prefix
            and cursor == len(original)
            and not original[-1].isspace()
            and any(command.path[: len(prefix)] == prefix for command in self.shell.commands)
        ):
            source += " "
        completion = self.shell.completion(source, len(source) if source != original else cursor)
        self.dismiss_suggestions()
        if completion.diagnostic:
            self.record(completion.diagnostic)
        if not completion.choices:
            return
        self._suggestion_snapshot = (original, cursor, source, completion)
        # Limit rendering. Legacy completers still enumerate all candidates;
        # a future bounded provider API can improve upstream performance.
        candidates = completion.choices[:50]
        shown = tuple(choice.replace("\r", "␍").replace("\n", "↵")[:120] for choice in candidates)
        self._suggestions.clear_options()
        self._suggestions.add_options(shown)
        self._suggestions.highlighted = 0
        self._suggestions.display = True
        more = " | showing first 50" if len(completion.choices) > 50 else ""
        self._suggestion_hint.update(
            f"Suggestions: {len(completion.choices)}{more} | ↑/↓ move | Enter insert | Esc close"
        )
        self._suggestion_hint.display = True
        prompt.focus()

    def move_suggestion(self, direction: int) -> None:
        if self._suggestion_snapshot is None:
            return
        count = self._suggestions.option_count
        if not count:
            return
        current = self._suggestions.highlighted
        self._suggestions.highlighted = ((0 if current is None else current) + direction) % count
        self._suggestions.scroll_to_highlight()

    def accept_suggestion(self, index: int | None = None) -> None:
        snapshot = self._suggestion_snapshot
        if snapshot is None:
            return
        original, cursor, source, completion = snapshot
        if self._prompt.value != original or self._prompt.cursor_position != cursor:
            self.dismiss_suggestions()
            return
        index = self._suggestions.highlighted if index is None else index
        if index is None or not 0 <= index < self._suggestions.option_count:
            return
        one = Completion(
            completion.start,
            completion.end,
            (completion.choices[index],),
            completion.quote,
        )
        value, position = one.apply(source)
        self.dismiss_suggestions()
        self._prompt._set(value)
        self._prompt.cursor_position = position
        self._prompt.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list is self._suggestions:
            event.stop()
            self.accept_suggestion(event.option_index)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input is self._prompt and self.suggestions_open:
            if event.value.strip():
                self._refresh_suggestions()
            else:
                self.dismiss_suggestions()

    def select_command(self, label: str) -> None:
        self.dismiss_suggestions()
        prompt = self._prompt
        prompt._set(label + " ")
        prompt.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if event.input.id == "search":
            self._filter = event.value.casefold()
            self._render_log()
            self._prompt.focus()
            return
        if self.suggestions_open:
            # Selecting a suggestion must NEVER execute the current command.
            self.accept_suggestion()
            return
        line = event.value.strip()
        if not line:
            return
        if self._thread is not None:
            # Don't eat a command an operator typed while waiting for hardware.
            # In particular, never queue it for automatic execution later.
            self.record("command running; input kept for later (Ctrl+C requests stop)")
            return
        event.input.value = ""
        self._active_label = self.shell.label(line)
        self._history.add(self._active_label)
        self.record(f"> {self._active_label}")
        self._completed.clear()
        self._cancel_request = threading.Event()
        self._outcome = None
        self._started_at = None
        self._finished_at = None
        context = contextvars.copy_context()

        def execute() -> None:
            self._started_at = time.monotonic()
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
                # Freeze duration before the UI drains any remaining output.
                self._finished_at = time.monotonic()
                self._completed.set()

        self._thread = threading.Thread(target=execute, name="autospan-console-command")
        self._thread.start()
        self._update_status()

    def _drain_output(self) -> None:
        if not self.is_running:
            return
        for _ in range(8):
            try:
                chunk = self._output.get_nowait()
            except queue.Empty:
                break
            for line in self._stream.push(chunk):
                self._record_line(line)
        if self._thread is not None and self._completed.is_set() and self._output.empty():
            # A handler may have written a final line without a newline.
            for line in self._stream.finish():
                self._record_line(line)
            self._thread.join()
            self._thread = None
            self._accept_outcome()
            if self._quit_requested:
                self.exit(self.status, return_code=self.status)
                return
        self._update_status()

    def _accept_outcome(self) -> None:
        outcome = self._outcome
        if self._started_at is not None and self._finished_at is not None:
            self._last_duration = self._finished_at - self._started_at
            self._started_at = None
        if outcome is None:
            return
        self._last_status = outcome.status
        if outcome.status:
            self._status = outcome.status if not self._status else max(self._status, outcome.status)
            self._failure_diagnostic = outcome.diagnostic
        self._quit_requested |= outcome.exit
        self._active_label = ""

    def _update_status(self) -> None:
        finished = self._finished_at if self._completed.is_set() else None
        elapsed = (
            (finished if finished is not None else time.monotonic()) - self._started_at
            if self._started_at is not None
            else self._last_duration
        )
        parts = [
            f"State: {self.operation_state}",
            f"Operation: {self._active_label or '-'}",
            f"Last status: {self._last_status if self._last_status is not None else '-'}",
            f"Elapsed: {elapsed:.1f}s" if elapsed is not None else "Elapsed: -",
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
        if self._thread is not None:
            if not self._completed.is_set():
                self._cancel_request.set()
                self.record("Stopping requested; waiting for the operation to stop or finish.")
                self._update_status()
            return

        # Only clear idle input on the main console screen.
        # Never interfere with an active modal or worker teardown.
        if self.screen is not self._prompt.screen:
            return

        self.dismiss_suggestions()
        if self._search.display:
            self._close_search()

        self._prompt._set("")
        self._history.add("")
        self.record("^C")
        self._prompt.focus()

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

    def on_key(self, event: events.Key) -> None:
        if self.suggestions_open and event.key == "escape":
            self.dismiss_suggestions()
            event.stop()
            event.prevent_default()
            return
        if (
            event.key == "escape"
            and self._search.display
            and self.screen is self._search.screen
            and (self._search.has_focus or self._prompt.has_focus)
        ):
            self._close_search()
            event.stop()
            event.prevent_default()

    def _render_log(self) -> None:
        self._log_view.clear()
        for line in self._log_text:
            if not self._filter or self._filter in line.casefold():
                self._log_view.write(line)

    def _close_search(self) -> None:
        self._search.display = False
        self._search.value = ""
        if self._filter:
            self._filter = ""
            self._render_log()
        self._prompt.focus()

    def action_toggle_sidebar(self) -> None:
        self._sidebar_override = not self._side.display
        self._refresh_sidebar()

    def action_follow_log(self) -> None:
        self._close_search()
        self._log_view.scroll_end(animate=False)

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
        Interruptions are re-raised after the command has finished.
        """
        thread = self._thread
        if thread is None:
            return
        self._cancel_request.set()
        errors: list[BaseException] = []
        while True:
            try:
                if not thread.is_alive():
                    break
                self._output.get(timeout=0.05)
            except queue.Empty:
                pass
            except BaseException as exc:
                errors.append(exc)
        try:
            thread.join()
        except BaseException as exc:
            errors.append(exc)
        self._thread = None
        self._accept_outcome()
        if errors:
            for error in errors[1:]:
                errors[0].add_note(f"Console shutdown interrupted again: {error!r}")
            raise errors[0]

    def on_unmount(self) -> None:
        self.wait_for_command()
