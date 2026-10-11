from __future__ import annotations

from contextlib import contextmanager
import json

import pytest

from autospan.api import (
    CommandContribution,
    CommandShell,
    ComponentSpec,
    LifecycleError,
    Runtime,
    RuntimePlan,
)
from autospan._core.host import HostBuild
from autospan.frontends.console import run_batch, run_console, script_lines


class _Evidence:
    root = "/tmp/evidence"


class _Thing:
    pass


def _shell(commands=(), instances=None) -> CommandShell:
    runtime = Runtime(instances or {}, _Evidence())  # type: ignore[arg-type]
    return CommandShell(runtime, commands)


def test_console_components_and_longest_command_match(capsys) -> None:
    calls: list[tuple[str, ...]] = []

    def short(runtime, args):
        calls.append(("short", *args))

    def long(runtime, args):
        calls.append(("long", *args))

    shell = _shell(
        (
            CommandContribution(("tool",), short, "short"),
            CommandContribution(("tool", "run"), long, "long"),
        ),
        {"thing": _Thing()},
    )
    assert shell.execute("components").status == 0
    assert "thing" in capsys.readouterr().out
    assert shell.execute("tool run target").status == 0
    assert calls == [("long", "target")]


def test_console_unknown_command_returns_nonzero(capsys) -> None:
    assert _shell().execute("does-not-exist").status == 2
    assert "unknown command" in capsys.readouterr().out


def test_console_parse_error_does_not_raise(capsys) -> None:
    assert _shell().execute('bad "quote').status == 2
    assert "parse error" in capsys.readouterr().out


def test_console_expected_command_error_shows_usage(capsys) -> None:
    def bad(runtime, args):
        raise ValueError("bad numeric argument")

    shell = _shell((CommandContribution(("tool",), bad, "bad", usage="NUMBER"),))
    assert shell.execute("tool").status == 2
    out = capsys.readouterr().out
    assert "command error: ValueError: bad numeric argument" in out
    assert "usage: tool NUMBER" in out


def test_console_unexpected_error_keeps_session_alive(capsys) -> None:
    def boom(runtime, args):
        raise RuntimeError("bug")

    shell = _shell((CommandContribution(("tool",), boom),))
    assert shell.execute("tool").status == 2
    assert "command failed: RuntimeError: bug" in capsys.readouterr().out


def test_capture_returns_output_instead_of_printing(capsys) -> None:
    def hello(ctx, args):
        ctx.write(" ".join(("hello", *args)))

    shell = _shell((CommandContribution(("say",), hello),))
    outcome = shell.execute("say world", capture=True)
    assert outcome.output == "hello world\n"
    assert capsys.readouterr().out == ""


def test_exit_and_help_topic(capsys) -> None:
    shell = _shell(
        (CommandContribution(("tool", "run"), lambda r, a: 0, "run it", usage="TARGET"),)
    )
    assert shell.execute("quit").exit is True
    assert shell.execute("help tool run").status == 0
    out = capsys.readouterr().out
    assert "tool run TARGET" in out and "run it" in out
    assert shell.execute("help nope").status == 2


def test_completion_paths_builtins_and_arguments() -> None:
    commands = (
        CommandContribution(("device", "start"), lambda r, a: 0),
        CommandContribution(("device", "list"), lambda r, a: 0),
        CommandContribution(
            ("job", "inspect"),
            lambda r, a: 0,
            complete=lambda r, a: ["item1", "item2"] if not a else [],
        ),
    )
    shell = _shell(commands)
    assert shell.complete("") == sorted(
        ["components", "evidence", "help", "exit", "quit", "device", "job"]
    )
    assert shell.complete("de") == ["device"]
    assert shell.complete("device ") == ["list", "start"]
    assert shell.complete("device s") == ["start"]
    assert shell.complete("job inspect ") == ["item1", "item2"]
    assert shell.complete("job inspect item2") == ["item2"]
    assert shell.complete("job inspect item1 ") == []


def test_commands_run_inside_evidence_steps() -> None:
    from contextlib import contextmanager

    steps: list[str] = []

    class Recording(_Evidence):
        @contextmanager
        def step(self, name, *, source="autospan", verdict=None):
            steps.append(name)
            yield

    shell = CommandShell(Runtime({}, Recording()), (CommandContribution(("go",), lambda r, a: 0),))  # type: ignore[arg-type]
    shell.execute("go now")
    assert steps == ["console: go"]


def test_script_lines_skip_comments_and_blanks() -> None:
    assert script_lines("# hi\n\n  a b  \n#c\nd\n") == ["a b", "d"]


def test_batch_stops_on_first_failure_unless_keep_going() -> None:
    seen: list[str] = []

    def fail(runtime, args):
        seen.append("fail")
        return 1

    def ok(runtime, args):
        seen.append("ok")

    shell = _shell((CommandContribution(("fail",), fail), CommandContribution(("ok",), ok)))
    assert run_batch(shell, ["fail", "ok"]) == 1
    assert seen == ["fail"]
    seen.clear()
    assert run_batch(shell, ["fail", "ok"], keep_going=True) == 1
    assert seen == ["fail", "ok"]


@pytest.mark.parametrize("phase", ["startup", "teardown"])
def test_console_manifest_records_runtime_failure(tmp_path, phase) -> None:
    class Factory:
        @contextmanager
        def open(self, context, dependencies, config):
            if phase == "startup":
                raise OSError("device failed")
            try:
                yield object()
            finally:
                raise OSError("device failed")

    host = HostBuild(
        plugins=(),
        plan=RuntimePlan(components=(ComponentSpec("device", "device"),)),
        factories={"device": Factory()},
        commands=(),
    )
    with pytest.raises(OSError if phase == "startup" else LifecycleError, match="device failed"):
        run_console(host=host, evidence_root=tmp_path, commands=("components",), plain=True)
    (manifest,) = tmp_path.glob("*/manifest.json")
    assert json.loads(manifest.read_text())["outcome"] == "failed"
