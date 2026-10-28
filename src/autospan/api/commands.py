"""Explicit command output and single-flight dispatch on the canonical runtime."""

from __future__ import annotations

import contextlib
import os
import re
import shlex
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from autospan.api.errors import AutoSpanError
from autospan.api.evidence import StepVerdict

if TYPE_CHECKING:
    from autospan.api.runtime import Runtime

OutputSink = Callable[[str], None]
ArgumentCompleter = Callable[["Runtime", tuple[str, ...]], Sequence[str]]
_BUILTINS = ("components", "evidence", "help", "exit", "quit")
OUTPUT_LIMIT = 65536


class CommandCancelled(AutoSpanError):
    """A handler acknowledged the operator's cooperative cancellation request."""


@dataclass(frozen=True, slots=True)
class CommandContext:
    """Live runtime, explicit text output and cooperative cancellation.

    Drivers remain owned by the runtime. A cancellation request does not stop a
    driver; check it between operations or wait on it during interruptible work.
    """

    runtime: Runtime
    _output: OutputSink
    _cancel: threading.Event

    def write(self, text: str) -> None:
        """Emit a line verbatim; the plugin must exclude secrets from this output."""
        self._output(str(text) + "\n")

    @property
    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def check_cancelled(self) -> None:
        """Acknowledge cancellation after the driver operation has stopped safely."""
        if self.cancel_requested:
            raise CommandCancelled("operation cancelled")

    def wait(self, seconds: float) -> bool:
        """Wait up to seconds; return True when cancellation is requested."""
        return self._cancel.wait(seconds)


CommandHandler = Callable[[CommandContext, tuple[str, ...]], int | None]


@dataclass(frozen=True, slots=True)
class CommandContribution:
    """One plugin command. Built-in roots are reserved; completers must be pure."""

    path: tuple[str, ...]
    handler: CommandHandler
    help: str = ""
    usage: str = ""
    complete: ArgumentCompleter | None = None
    # None keeps conservative legacy diagnostics; () explicitly declares all public.
    # Explicit sensitive positions also mask handler writes, not arbitrary plugin I/O.
    sensitive_args: tuple[int, ...] | None = None

    def __post_init__(self) -> None:
        if not self.path or any(
            not token or any(c.isspace() for c in token) for token in self.path
        ):
            raise ValueError("Command path must contain non-empty tokens without whitespace")
        if self.path[0] in _BUILTINS:
            raise ValueError(f"Reserved console command root: {self.path[0]}")
        if self.sensitive_args is not None:
            indices = tuple(self.sensitive_args)
            if any(type(index) is not int or index < 0 for index in indices):
                raise ValueError("sensitive_args must contain nonnegative argument indices")
            object.__setattr__(self, "sensitive_args", tuple(dict.fromkeys(indices)))


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    """Status, bounded captured output, exit intent and a sanitized diagnostic.

    error retains the original cause for callers; do not persist its raw
    representation because plugin exceptions may include sensitive arguments.
    """

    status: int
    output: str = ""
    exit: bool = False
    diagnostic: str = ""
    error: BaseException | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class Completion:
    """Decoded candidates and the complete token span to replace at the cursor."""

    start: int
    end: int
    choices: tuple[str, ...]
    quote: str = ""
    diagnostic: str = ""

    def apply(self, line: str) -> tuple[str, int]:
        """Replace only this token, retaining text after the cursor."""
        if not self.choices:
            return line, self.start
        value = os.path.commonprefix(self.choices)
        if self.quote and self.quote not in value:
            value = self.quote + value + (self.quote if len(self.choices) == 1 else "")
        else:
            value = shlex.quote(value) if any(c.isspace() or c in "\"'" for c in value) else value
        suffix = line[self.end :]
        if len(self.choices) == 1 and not suffix.startswith((" ", "\t")):
            value += " "
        return line[: self.start] + value + suffix, self.start + len(value)


@dataclass(frozen=True, slots=True)
class _Token:
    value: str
    start: int
    end: int
    quote: str = ""


def _tokens(line: str, *, incomplete: bool = False) -> list[_Token]:
    # Backslashes are literal so Windows paths survive on every host. Quote
    # paths containing spaces rather than relying on platform-specific escapes.
    result: list[_Token] = []
    position = 0
    while position < len(line):
        if line[position].isspace():
            position += 1
            continue
        start = position
        quote = ""
        value: list[str] = []
        while position < len(line):
            char = line[position]
            if quote:
                if char == quote:
                    quote = ""
                else:
                    value.append(char)
            elif char in "\"'":
                quote = char
            elif char.isspace():
                break
            else:
                value.append(char)
            position += 1
        if quote and not incomplete:
            raise ValueError("unfinished quote")
        initial_quote = line[start] if line[start] in "\"'" else ""
        result.append(_Token("".join(value), start, position, initial_quote))
    return result


def _redacted(text: str, args: tuple[str, ...], sensitive: tuple[int, ...] | None) -> str:
    selected = args if sensitive is None else tuple(args[i] for i in sensitive if i < len(args))
    values = {arg for arg in selected if arg}
    values.update(arg.partition("=")[2] for arg in selected if "=" in arg and arg.partition("=")[2])
    patterns = []
    for value in sorted(values, key=lambda item: (-len(item), item)):
        pattern = re.escape(value)
        # A numeric argument must not rewrite digits inside another value.
        if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", value):
            pattern = r"(?<![\w.])" + pattern + r"(?![\w.])"
        patterns.append(pattern)
    return re.sub("|".join(patterns), "<redacted>", text) if patterns else text


def _sanitized(
    error: BaseException, args: tuple[str, ...], sensitive: tuple[int, ...] | None = None
) -> str:
    """Mask declared values; never serialize the original exception or traceback."""
    return f"{type(error).__name__}: {_redacted(str(error), args, sensitive)}"[:4096]


class CommandShell:
    """Shared dispatcher: reject overlaps, retain failures, never redirect streams.

    Only contributed invocations create evidence steps. Built-ins, invalid input
    and busy rejections do not. Any executed failure makes the session fail.
    """

    def __init__(self, runtime: Runtime, commands: Sequence[CommandContribution]) -> None:
        self._runtime = runtime
        self._commands = tuple(commands)
        paths = [command.path for command in self._commands]
        if len(paths) != len(set(paths)):
            raise ValueError("Duplicate console command path")
        self._flight = threading.Lock()
        self._active = threading.Event()
        self._cancel = threading.Event()
        self._status = 0

    @property
    def commands(self) -> tuple[CommandContribution, ...]:
        return self._commands

    @property
    def status(self) -> int:
        return self._status

    @property
    def state(self) -> str:
        if self._active.is_set():
            return "stopping" if self._cancel.is_set() else "running"
        return "failed" if self._status else "idle"

    @property
    def evidence_root(self) -> str:
        return str(getattr(self._runtime.evidence, "root", "<no local evidence path>"))

    def cancel(self) -> None:
        """Request cooperation; the caller must still wait for execute to return."""
        if self._active.is_set():
            self._cancel.set()

    def label(self, line: str) -> str:
        """Safe display/history label: command path only, never argument text."""
        try:
            tokens = tuple(token.value for token in _tokens(line))
        except ValueError:
            return "<invalid input>"
        command = self._match(tokens)
        if command is not None:
            return " ".join(command.path)
        return tokens[0] if tokens and tokens[0] in _BUILTINS else "<unknown command>"

    def execute(
        self,
        line: str,
        *,
        capture: bool = False,
        output: OutputSink | None = None,
        cancellation: threading.Event | None = None,
    ) -> CommandOutcome:
        """Execute synchronously, or reject when busy. Capture explicit writes only.

        Captured text retains at most 64K characters. A supplied sink receives streaming
        output and controls its own buffering/backpressure. Diagnostics are also
        returned separately so truncation cannot hide the verdict.
        """
        text = ""
        truncated = False

        def write(chunk: str) -> None:
            nonlocal text, truncated
            if output is not None:
                output(chunk)
            elif capture:
                truncated |= len(text) + len(chunk) > OUTPUT_LIMIT
                text = (text + chunk)[-OUTPUT_LIMIT:]
            else:
                print(chunk, end="")

        if not self._flight.acquire(blocking=False):
            diagnostic = "command running; wait for completion or request cancellation"
            write(diagnostic + "\n")
            return CommandOutcome(2, text, diagnostic=diagnostic)
        if cancellation is None:
            self._cancel = threading.Event()
        else:
            self._cancel = cancellation
        self._active.set()
        try:
            outcome = self._run(line, write)
            if outcome.status:
                self._status = (
                    outcome.status if not self._status else max(self._status, outcome.status)
                )
            if truncated:
                text = "[earlier output truncated]\n" + text
            return CommandOutcome(
                outcome.status, text, outcome.exit, outcome.diagnostic, outcome.error
            )
        finally:
            self._active.clear()
            self._flight.release()

    def help_lines(self) -> list[str]:
        lines = [
            "help [COMMAND]                 list commands or show usage",
            "components                     list live components",
            "evidence                       show the evidence location",
        ]
        for command in sorted(self._commands, key=lambda item: item.path):
            label = " ".join(command.path)
            lines.append(f"{label:30} {command.help}")
        lines.append("exit / quit                    leave the console")
        return lines

    def complete(self, line: str, cursor: int | None = None) -> list[str]:
        """Suggest at the cursor, accepting incomplete quotes and literal backslashes."""
        return list(self.completion(line, cursor).choices)

    def completion(self, line: str, cursor: int | None = None) -> Completion:
        cursor = len(line) if cursor is None else max(0, min(cursor, len(line)))
        tokens = _tokens(line, incomplete=True)
        current = next((t for t in tokens if t.start <= cursor <= t.end), None)
        typed = tuple(t.value for t in tokens if t.end < cursor and t is not current)
        start, end = (current.start, current.end) if current else (cursor, cursor)
        partial = _tokens(line[start:cursor], incomplete=True)
        prefix = partial[0].value if partial else ""
        names: set[str] = set()
        lookup = typed[1:] if typed[:1] == ("help",) else typed
        if not lookup:
            names.update(_BUILTINS)
        diagnostic = ""
        available = self._flight.acquire(blocking=False)
        try:
            for command in self._commands:
                path = command.path
                if len(lookup) < len(path) and path[: len(lookup)] == lookup:
                    names.add(path[len(lookup)])
                elif typed[: len(path)] == path and command.complete is not None and available:
                    try:
                        names.update(command.complete(self._runtime, typed[len(path) :]))
                    except Exception as exc:
                        diagnostic = f"completion failed: {_sanitized(exc, typed[len(path) :], command.sensitive_args)}"
        finally:
            if available:
                self._flight.release()
        return Completion(
            start,
            end,
            tuple(sorted(n for n in names if n.startswith(prefix))),
            current.quote if current else "",
            diagnostic,
        )

    def _match(self, tokens: tuple[str, ...]) -> CommandContribution | None:
        matches = [c for c in self._commands if tokens[: len(c.path)] == c.path]
        return max(matches, key=lambda c: len(c.path)) if matches else None

    def _run(self, line: str, write: OutputSink) -> CommandOutcome:
        try:
            tokens = tuple(token.value for token in _tokens(line))
        except ValueError as exc:
            diagnostic = f"parse error: {exc}"
            write(diagnostic + "\n")
            return CommandOutcome(2, diagnostic=diagnostic, error=exc)
        if not tokens:
            return CommandOutcome(0)
        if tokens in {("exit",), ("quit",)}:
            return CommandOutcome(0, exit=True)
        if tokens == ("components",):
            for name in self._runtime.names():
                write(name + "\n")
            return CommandOutcome(0)
        if tokens == ("evidence",):
            write(self.evidence_root + "\n")
            return CommandOutcome(0)
        if tokens[0] == "help":
            topic = tokens[1:]
            if not topic:
                for line in self.help_lines():
                    write(line + "\n")
                return CommandOutcome(0)
            for help_command in self._commands:
                if help_command.path == topic:
                    write(f"{' '.join(help_command.path)} {help_command.usage}\n".lstrip())
                    write(help_command.help + "\n")
                    return CommandOutcome(0)
            if len(topic) == 1 and topic[0] in _BUILTINS:
                for line in self.help_lines():
                    if (
                        line.split()[0] == topic[0]
                        or topic[0] == "quit"
                        and line.startswith("exit")
                    ):
                        write(line + "\n")
                        return CommandOutcome(0)
        command = self._match(tokens)
        if command is None:
            diagnostic = "unknown command; use 'help' to list commands"
            write(diagnostic + "\n")
            return CommandOutcome(2, diagnostic=diagnostic)
        return self._invoke(command, tokens[len(command.path) :], write)

    def _invoke(
        self, command: CommandContribution, args: tuple[str, ...], write: OutputSink
    ) -> CommandOutcome:
        verdict = StepVerdict()
        error: BaseException | None = None
        diagnostic = ""

        def handler_write(text: str) -> None:
            write(
                _redacted(text, args, command.sensitive_args)
                if command.sensitive_args is not None
                else text
            )

        step = getattr(self._runtime.evidence, "step", None)
        try:
            scope = (
                step(f"console: {' '.join(command.path)}", source="console", verdict=verdict)
                if step is not None
                else contextlib.nullcontext()
            )
            with scope:
                try:
                    context = CommandContext(self._runtime, handler_write, self._cancel)
                    verdict.status = int(command.handler(context, args) or 0)
                except (CommandCancelled, KeyboardInterrupt) as exc:
                    error = exc
                    verdict.status = 130
                    diagnostic = "operation cancelled after command stopped"
                except BaseException as exc:
                    error = exc
                    verdict.status = 2
                    expected = isinstance(exc, (AutoSpanError, ValueError, TypeError, KeyError))
                    prefix = "command error" if expected else "command failed"
                    diagnostic = f"{prefix}: {_sanitized(exc, args, command.sensitive_args)}"
                    if expected and command.usage:
                        diagnostic += f"\nusage: {' '.join(command.path)} {command.usage}"
                if error is not None:
                    verdict.error = _sanitized(error, args, command.sensitive_args)
                if verdict.status and not diagnostic:
                    diagnostic = f"command returned status {verdict.status}"
                if diagnostic:
                    write(diagnostic + "\n")
        except Exception as exc:
            if error is not None:
                error.add_note(
                    f"Command evidence failed: {_sanitized(exc, args, command.sensitive_args)}"
                )
            else:
                error = exc
            verdict.status = max(verdict.status, 2)
            diagnostic += (
                "\n" if diagnostic else ""
            ) + f"command evidence failed: {_sanitized(exc, args, command.sensitive_args)}"
            write(diagnostic + "\n")
        return CommandOutcome(verdict.status, diagnostic=diagnostic, error=error)
