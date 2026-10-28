"""Smoke and operator invariants for the isolated no-hardware Lab."""

from __future__ import annotations

import asyncio
import threading

import pytest

from autospan.demos.lab import make_shell
from autospan.api import CommandContext


def run(shell, line, *, cancellation=None):
    output = []
    outcome = shell.execute(line, output=output.append, cancellation=cancellation)
    return outcome, "".join(output)


def test_commands_and_dynamic_completion(tmp_path):
    shell = make_shell(tmp_path)
    outcome, text = run(shell, "lab devices")
    assert outcome.status == 0
    assert "controller" in text and "sensor-a" in text and "power" in text

    choices = shell.completion("lab select ").choices
    assert "sensor-a" in choices and "power" in choices
    assert "temperature" in shell.completion("lab read ").choices

    assert run(shell, "lab select power")[0].status == 0
    assert "relay" in shell.completion("lab read ").choices
    assert "temperature" not in shell.completion("lab read ").choices
    assert "relay" in shell.completion("lab set ").choices
    assert run(shell, "lab set relay on")[0].status == 0
    assert "power.relay = on" in run(shell, "lab read relay")[1]


@pytest.mark.parametrize("initial_relay", ["off", "on"])
def test_smoke_cleanup_and_synthetic_failure(tmp_path, initial_relay, monkeypatch):
    monkeypatch.setattr(CommandContext, "wait", lambda self, seconds: self.cancel_requested)
    shell = make_shell(tmp_path)
    assert run(shell, "lab select power")[0].status == 0
    assert run(shell, f"lab set relay {initial_relay}")[0].status == 0
    good, output = run(shell, "lab run smoke")
    assert good.status == 0
    assert "PASS smoke" in output
    assert "CLEANUP OK" in output
    assert f"power.relay = {initial_relay}" in run(shell, "lab read relay")[1]

    failed, output = run(shell, "lab run failure")
    assert failed.status != 0
    assert "synthetic fault" in output
    assert "CLEANUP OK" in output
    assert f"power.relay = {initial_relay}" in run(shell, "lab read relay")[1]


def test_cancellation_after_relay_change_restores_state(tmp_path, monkeypatch):
    monkeypatch.setattr(CommandContext, "wait", lambda self, seconds: self.cancel_requested)
    shell = make_shell(tmp_path)
    cancellation = threading.Event()
    output = []

    def sink(text):
        output.append(text)
        # Step 3 already changed the relay; cancel during step 4.
        if "[4/4]" in text:
            cancellation.set()

    outcome = shell.execute("lab run smoke", output=sink, cancellation=cancellation)
    assert outcome.status == 130
    assert "CLEANUP OK" in "".join(output) and "PASS smoke" not in "".join(output)
    run(shell, "lab select power")
    assert "power.relay = off" in run(shell, "lab read relay")[1]


def test_seeded_stream_has_complete_lines_and_no_evidence_files(tmp_path, monkeypatch):
    monkeypatch.setattr(CommandContext, "wait", lambda self, seconds: self.cancel_requested)
    first = run(make_shell(tmp_path, seed=7), "lab stream")[1]
    assert first == run(make_shell(tmp_path, seed=7), "lab stream")[1]
    assert first != run(make_shell(tmp_path, seed=8), "lab stream")[1]
    lines = first.splitlines()
    assert len(lines) == 62
    assert all(
        line.startswith(f"sample {index:03d} | sensor-a | temperature=") and line.endswith(" C")
        for index, line in enumerate(lines[1:-1])
    )
    assert list(tmp_path.iterdir()) == []


def test_completed_rate_suggestion_round_trips_and_sessions_are_isolated(tmp_path):
    shell = make_shell(tmp_path)
    completion = shell.completion("lab set sample_rate 2")
    line, _ = completion.apply("lab set sample_rate 2")
    assert run(shell, line)[0].status == 0
    assert "20 Hz" in run(shell, "lab read sample_rate")[1]
    assert "10 Hz" in run(make_shell(tmp_path), "lab read sample_rate")[1]


def test_cancelled_run_restores_local_state(tmp_path):
    shell = make_shell(tmp_path)
    cancellation = threading.Event()
    cancellation.set()
    outcome, text = run(shell, "lab run endurance", cancellation=cancellation)
    assert outcome.status == 130
    assert "CLEANUP OK" in text


def test_validation_and_developer_only_command(tmp_path):
    shell = make_shell(tmp_path)
    assert run(shell, "lab read unknown")[0].status != 0
    assert run(shell, "lab select nowhere")[0].status != 0
    assert run(shell, "lab set mode active")[0].status != 0
    assert "lab stubborn" not in " ".join(" ".join(c.path) for c in shell.commands)
    dev = make_shell(tmp_path, developer=True)
    assert ("lab", "stubborn") in tuple(c.path for c in dev.commands)


def test_headless_suggestions_do_not_execute(tmp_path):
    pytest.importorskip("textual")
    from autospan.frontends.tui.app import ConsoleApp, PromptInput

    async def scenario():
        app = ConsoleApp(make_shell(tmp_path))
        async with app.run_test(size=(80, 24)) as pilot:
            prompt = app.query_one(PromptInput)
            prompt.value = "lab "
            prompt.cursor_position = len(prompt.value)
            await pilot.press("ctrl+space")
            await pilot.pause()
            assert app.suggestions_open
            assert app._suggestions.option_count >= 5
            await pilot.press("enter")
            await pilot.pause()
            assert not app.suggestions_open
            assert app.operation_state == "idle"
            assert prompt.value.startswith("lab ")
            prompt.value = "lab "
            prompt.cursor_position = len(prompt.value)
            await pilot.press("ctrl+space")
            await pilot.pause()
            assert app.suggestions_open
            await pilot.press("escape")
            assert not app.suggestions_open

    asyncio.run(scenario())
