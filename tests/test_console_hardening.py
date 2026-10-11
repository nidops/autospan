"""Console verdicts, concurrency and shutdown against real evidence files."""

from __future__ import annotations

import json
import re
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from autospan.api import (
    AutoSpanError,
    CommandContribution,
    CommandShell,
    ComponentSpec,
    Runtime,
    RuntimePlan,
    StepVerdict,
)
from autospan._core.evidence_store import LocalEvidenceStore
from autospan._core.host import HostBuild
from autospan.frontends.console import run_console


def records(root):
    (path,) = root.glob("*/events.jsonl")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize(
    "result",
    [0, 7, -1, AutoSpanError("device error"), ValueError("invalid number"), RuntimeError("bug")],
)
def test_command_verdict_and_run_manifest_agree(tmp_path, result):
    def command(ctx, args):
        ctx.runtime.evidence.event("inside_command")
        if isinstance(result, Exception):
            raise result
        return result

    host = HostBuild((), RuntimePlan(()), {}, (CommandContribution(("go",), command),))
    code = run_console(host=host, evidence_root=tmp_path, commands=("go",), plain=True)
    assert code == (2 if isinstance(result, Exception) else result)
    events = records(tmp_path)
    started = [e for e in events if e["name"] == "step_started"]
    finished = [e for e in events if e["name"] == "step_finished"]
    assert len(started) == len(finished) == 1
    verdict = "failed" if code else "passed"
    assert finished[0]["attributes"]["outcome"] == verdict
    (inside,) = (e for e in events if e["name"] == "inside_command")
    assert inside["step_id"] == started[0]["step_id"] == finished[0]["step_id"]
    assert inside["run_id"] == started[0]["run_id"]
    if isinstance(result, Exception):
        assert type(result).__name__ in finished[0]["attributes"]["error"]
    (manifest,) = tmp_path.glob("*/manifest.json")
    assert json.loads(manifest.read_text())["outcome"] == verdict


def test_explicit_verdict_preserves_generic_step_context(tmp_path):
    evidence = LocalEvidenceStore(tmp_path)
    verdict = StepVerdict()
    with evidence.scope(test_id="test-a"):
        with evidence.step("operation", verdict=verdict) as context:
            assert context.test_id == "test-a"
            verdict.status = 3
        with evidence.step("ordinary"):
            pass
    evidence.close(outcome="failed")
    finished = [e for e in records(tmp_path) if e["name"] == "step_finished"]
    assert [e["attributes"]["outcome"] for e in finished] == ["failed", "passed"]


@pytest.mark.parametrize("failed_event", ["step_started", "step_finished"])
def test_evidence_write_failure_reports_failure_and_closes_component(
    tmp_path, monkeypatch, failed_event
):
    closed = []

    class Factory:
        @contextmanager
        def open(self, context, dependencies, config):
            try:
                yield object()
            finally:
                closed.append(True)

    original = LocalEvidenceStore.event

    def event(self, name, **attributes):
        if name == failed_event:
            raise OSError("evidence disk failed")
        return original(self, name, **attributes)

    monkeypatch.setattr(LocalEvidenceStore, "event", event)
    host = HostBuild(
        (),
        RuntimePlan((ComponentSpec("device", "device"),)),
        {"device": Factory()},
        (CommandContribution(("go",), lambda r, a: 0),),
    )
    assert run_console(host=host, evidence_root=tmp_path, commands=("go",), plain=True) == 2
    assert closed == [True]
    (manifest,) = tmp_path.glob("*/manifest.json")
    assert json.loads(manifest.read_text())["outcome"] == "failed"


def test_evidence_finalization_failure_is_not_suppressed(tmp_path, monkeypatch):
    def close(self, **kwargs):
        self._events_file.close()
        raise OSError("manifest disk failed")

    monkeypatch.setattr(LocalEvidenceStore, "close", close)
    host = HostBuild((), RuntimePlan(()), {}, ())
    with pytest.raises(OSError, match="manifest disk failed"):
        run_console(host=host, evidence_root=tmp_path, commands=("components",), plain=True)


def shell_at(root, handler, *, complete=None):
    evidence = LocalEvidenceStore(root)
    shell = CommandShell(
        Runtime({}, evidence), (CommandContribution(("tool", "run"), handler, complete=complete),)
    )
    return shell, evidence


def test_explicit_output_does_not_capture_background_stdout_or_stderr(tmp_path, capsys):
    started, release = threading.Event(), threading.Event()
    original_stdout, original_stderr = sys.stdout, sys.stderr

    def command(ctx, args):
        started.set()
        assert release.wait(3)
        ctx.write("command output")

    shell, evidence = shell_at(tmp_path, command)
    outcomes = []
    worker = threading.Thread(
        target=lambda: outcomes.append(shell.execute("tool run", capture=True))
    )
    worker.start()
    try:
        assert started.wait(3)
        print("background output")
        print("background error", file=sys.stderr)
        assert sys.stdout is original_stdout and sys.stderr is original_stderr
    finally:
        release.set()
        worker.join(3)
        evidence.close(outcome="passed")
    assert not worker.is_alive()
    assert outcomes[0].output == "command output\n"
    captured = capsys.readouterr()
    assert captured.out == "background output\n" and captured.err == "background error\n"


def test_shell_rejects_overlap_and_waits_for_cooperative_cancellation(tmp_path):
    started = threading.Event()
    calls = []

    def command(ctx, args):
        calls.append(args)
        started.set()
        assert ctx.wait(3)
        ctx.check_cancelled()

    shell, evidence = shell_at(tmp_path, command)
    outcomes = []
    worker = threading.Thread(
        target=lambda: outcomes.append(shell.execute("tool run one", capture=True))
    )
    worker.start()
    try:
        assert started.wait(3)
        assert shell.state == "running"
        busy = shell.execute("tool run two", capture=True)
        assert busy.status == 2 and "command running" in busy.diagnostic
        shell.cancel()
    finally:
        shell.cancel()
        worker.join(3)
        evidence.close(outcome="failed")
    assert not worker.is_alive()
    assert calls == [("one",)]
    assert outcomes[0].status == 130
    assert shell.state == "failed"
    steps = [e for e in records(tmp_path) if e["name"] == "step_finished"]
    assert len(steps) == 1 and steps[0]["attributes"]["outcome"] == "failed"


def test_sensitive_arguments_are_not_in_evidence_or_batch_echo(tmp_path, capsys):
    secret = "private-password-123"
    cause = ValueError(f"invalid token {secret}")

    def command(ctx, args):
        raise cause

    shell, evidence = shell_at(tmp_path, command)
    result = shell.execute(f"tool run --password={secret}", capture=True)
    assert result.error is cause
    assert secret not in result.output + result.diagnostic
    assert shell.label(f"tool run {secret}") == "tool run"
    evidence.close(outcome="failed")
    assert all(secret not in p.read_text() for p in evidence.root.glob("*.json*"))
    host = HostBuild((), RuntimePlan(()), {}, (CommandContribution(("go",), command),))
    assert (
        run_console(
            host=host, evidence_root=tmp_path / "batch", commands=(f"go {secret}",), plain=True
        )
        == 2
    )
    assert secret not in capsys.readouterr().out


def test_bounded_output_keeps_critical_failure_diagnostic(tmp_path):
    def command(ctx, args):
        for _ in range(100):
            ctx.write("雪" * 4000)
        return 9

    shell, evidence = shell_at(tmp_path, command)
    result = shell.execute("tool run", capture=True)
    assert result.status == 9 and "status 9" in result.diagnostic
    assert len(result.output) <= 65536 + 64
    assert "earlier output truncated" in result.output and "status 9" in result.output
    evidence.close(outcome="failed")


def test_redaction_contract_does_not_hide_non_argument_content(tmp_path):
    def command(ctx, args):
        ctx.write("explicit plugin output")
        raise ValueError(f"configuration-value-123 rejected {args[0]}")

    shell, evidence = shell_at(tmp_path, command)
    result = shell.execute("tool run argument-value-456", capture=True)
    evidence.close(outcome="failed")
    assert "argument-value-456" not in result.output + result.diagnostic
    assert "configuration-value-123" in result.diagnostic
    assert "explicit plugin output" in result.output


@pytest.mark.parametrize(
    "line, expected",
    [
        ('tool run "al', ["alpha beta", "alpha gamma"]),
        ("tool run 'alpha b", ["alpha beta"]),
        (r"tool run C:\\Pro", [r"C:\\Program Files\\output.txt"]),
        ("help tool r", ["run"]),
    ],
)
def test_completion_accepts_quotes_paths_and_nested_commands(tmp_path, line, expected):
    options = ["alpha beta", "alpha gamma", r"C:\\Program Files\\output.txt", "alpha beta"]
    shell, evidence = shell_at(tmp_path, lambda c, a: 0, complete=lambda r, a: options)
    assert shell.complete(line) == expected
    evidence.close(outcome="passed")


def test_cursor_middle_completion_replaces_token_and_preserves_suffix(tmp_path):
    received = []
    shell, evidence = shell_at(
        tmp_path, lambda c, a: received.append(a), complete=lambda r, a: ["alpha beta"]
    )
    line = 'tool run "alzzz" tail'
    completion = shell.completion(line, len('tool run "al'))
    value, cursor = completion.apply(line)
    assert value == 'tool run "alpha beta" tail'
    assert value[:cursor] == 'tool run "alpha beta"'
    assert shell.execute(value, capture=True).status == 0
    assert received == [("alpha beta", "tail")]
    received.clear()
    assert shell.execute(r'tool run "C:\Program Files\output.txt"', capture=True).status == 0
    assert received == [(r"C:\Program Files\output.txt",)]
    evidence.close(outcome="passed")


@pytest.mark.parametrize("name", ["help", "components", "evidence", "exit", "quit"])
def test_reserved_commands_are_rejected_before_host_acquisition(name):
    with pytest.raises(ValueError, match="Reserved console command"):
        CommandContribution((name, "nested"), lambda c, a: 0)


def test_duplicate_commands_are_rejected_for_direct_shells(tmp_path):
    shell, evidence = shell_at(tmp_path, lambda c, a: 0)
    with pytest.raises(ValueError, match="Duplicate console"):
        CommandShell(Runtime({}, evidence), (shell.commands[0], shell.commands[0]))
    evidence.close(outcome="passed")


def test_empty_script_remains_noninteractive(tmp_path, monkeypatch):
    script = tmp_path / "empty.txt"
    script.write_text("# no operations\n\n", encoding="utf-8")

    def forbidden(*args):
        raise AssertionError("batch script requested interactive input")

    monkeypatch.setattr("builtins.input", forbidden)
    host = HostBuild((), RuntimePlan(()), {}, ())
    assert run_console(host=host, evidence_root=tmp_path / "evidence", script=script) == 0


def test_utf8_script_keep_going_preserves_failure(tmp_path):
    received = []

    def command(ctx, args):
        received.append(args)
        return 3 if args == ("fail",) else 0

    script = tmp_path / "unicode.txt"
    script.write_text('# comment\n go fail\n\n go "雪 data"\n', encoding="utf-8")
    host = HostBuild((), RuntimePlan(()), {}, (CommandContribution(("go",), command),))
    assert (
        run_console(host=host, evidence_root=tmp_path / "evidence", script=script, keep_going=True)
        == 3
    )
    assert received == [("fail",), ("雪 data",)]
    (manifest,) = (tmp_path / "evidence").glob("*/manifest.json")
    assert json.loads(manifest.read_text())["outcome"] == "failed"


def test_plain_repl_retains_failure_after_success_and_quit(tmp_path, monkeypatch):
    lines = iter(("go", "help", "quit"))
    monkeypatch.setattr("builtins.input", lambda prompt: next(lines))
    monkeypatch.setattr("autospan.frontends.console._enable_line_editing", lambda shell: None)
    host = HostBuild((), RuntimePlan(()), {}, (CommandContribution(("go",), lambda c, a: 4),))
    assert run_console(host=host, evidence_root=tmp_path, plain=True) == 4
    (manifest,) = tmp_path.glob("*/manifest.json")
    assert json.loads(manifest.read_text())["outcome"] == "failed"


def test_console_manual_command_definition_runs(tmp_path):
    manual = Path(__file__).resolve().parents[1] / "docs" / "CONSOLE.md"
    (definition,) = re.findall(r"```python\n(.*?)\n```", manual.read_text(encoding="utf-8"), re.S)
    namespace = {}
    exec(definition, namespace)
    evidence = LocalEvidenceStore(tmp_path)
    try:
        shell = CommandShell(Runtime({}, evidence), (namespace["STATUS"],))
        result = shell.execute("project status", capture=True)
        assert result.status == 0 and result.output == "no components\n"
    finally:
        evidence.close(outcome="passed")


def test_batch_interrupt_stops_even_with_keep_going(tmp_path):
    seen = []

    def interrupted(ctx, args):
        seen.append("interrupted")
        raise KeyboardInterrupt

    def next_command(ctx, args):
        seen.append("next")

    host = HostBuild(
        (),
        RuntimePlan(()),
        {},
        (
            CommandContribution(("go",), interrupted),
            CommandContribution(("next",), next_command),
        ),
    )
    assert (
        run_console(host=host, evidence_root=tmp_path, commands=("go", "next"), keep_going=True)
        == 130
    )
    assert seen == ["interrupted"]
