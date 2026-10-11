from __future__ import annotations

import asyncio
import json
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest
from autospan.api import (
    CommandContribution,
    CommandShell,
    ComponentSpec,
    LifecycleError,
    ResourceClaim,
    Runtime,
    RuntimePlan,
)
from autospan._core.evidence_store import LocalEvidenceStore
from autospan._core.host import HostBuild
from autospan._core.runtime import RuntimeSession
from autospan.frontends.console import run_console

pytest.importorskip("textual")

from autospan.frontends.tui.app import ConsoleApp, ConsoleCommands, PromptInput  # noqa: E402
from autospan.frontends.tui.editing import History  # noqa: E402
from textual.widgets import Input, RichLog  # noqa: E402


class _Evidence:
    root = "/tmp/e"


def _shell():
    def hello(ctx, args):
        ctx.write(" ".join(("hello", *args)))

    commands = (
        CommandContribution(("say", "hello"), hello, "greet"),
        CommandContribution(("say", "helium"), hello, "element"),
    )
    return CommandShell(Runtime({"thing": object()}, _Evidence()), commands)


async def until(pilot, predicate, *, timeout=5):
    async with asyncio.timeout(timeout):
        while not predicate():
            await pilot.pause()
            await asyncio.sleep(0.005)


def test_history_navigation_restores_draft_and_bounds_retention():
    history = History(limit=3)
    for line in ("a", "b", "b", "c", "d"):
        history.add(line)
    assert history.items == ("b", "c", "d")
    assert history.previous("draft") == "d"
    assert history.previous("") == "c"
    assert history.next("") == "d"
    assert history.next("") == "draft"


def test_app_runs_commands_and_cursor_completion():
    async def scenario():
        app = ConsoleApp(_shell(), ("thing",))
        async with app.run_test() as pilot:
            prompt = app.query_one(PromptInput)
            prompt.value = "say hello wor"
            await pilot.press("enter")
            await until(pilot, lambda: app.operation_state == "idle")
            prompt.value = "say hezz tail"
            prompt.cursor_position = len("say he")
            await pilot.press("tab")
            assert prompt.value == "say hel tail"
            assert prompt.cursor_position == len("say hel")
            await pilot.press("tab")
            assert prompt.value == "say helium tail"
            await pilot.press("tab")
            assert prompt.value == "say hello tail"
            await pilot.press("shift+tab")
            assert prompt.value == "say helium tail"
            assert "hello wor" in app.retained_log
            prompt.value = "quit"
            await pilot.press("enter")
            await until(pilot, lambda: app.return_code is not None)
        assert app.return_code == 0

    asyncio.run(scenario())


def test_quoted_completion_cycles_preserve_suffix_and_call_completer_once():
    calls = []

    def complete(runtime, args):
        calls.append(args)
        return ("some alpha", "some beta")

    shell = CommandShell(
        Runtime({}, _Evidence()),
        (CommandContribution(("open",), lambda ctx, args: None, complete=complete),),
    )

    async def scenario():
        app = ConsoleApp(shell)
        async with app.run_test() as pilot:
            prompt = app.query_one(PromptInput)
            prompt.value = 'open "so" untouched'
            prompt.cursor_position = len('open "so')
            await pilot.press("tab")
            await pilot.press("tab")
            assert prompt.value == 'open "some alpha" untouched'
            await pilot.press("tab")
            assert prompt.value == 'open "some beta" untouched'
            await pilot.press("tab")
            assert prompt.value == 'open "some alpha" untouched'
            await pilot.press("shift+tab")
            assert prompt.value == 'open "some beta" untouched'
            assert calls == [()]

    asyncio.run(scenario())


@pytest.mark.parametrize("edit", ["cursor", "text"])
def test_completion_cycle_resets_after_editing_away_and_back(edit):
    async def scenario():
        app = ConsoleApp(_shell())
        async with app.run_test() as pilot:
            prompt = app.query_one(PromptInput)
            prompt.value = "say he"
            prompt.cursor_position = len(prompt.value)
            await pilot.press("tab")
            assert prompt.value == "say hel"
            if edit == "cursor":
                await pilot.press("left", "right")
            else:
                await pilot.press("x", "backspace")
            await pilot.press("tab")
            assert prompt.value == "say hel"
            await pilot.press("shift+tab")
            assert prompt.value == "say hello "

    asyncio.run(scenario())


@pytest.mark.parametrize("cooperative", [False, True])
def test_quit_joins_command_before_runtime_teardown(tmp_path, monkeypatch, cooperative):
    started, release = threading.Event(), threading.Event()
    order = []

    class Factory:
        @contextmanager
        def open(self, context, dependencies, config):
            order.append("open")
            try:
                yield object()
            finally:
                order.append("close")

    def command(ctx, args):
        order.append("command")
        started.set()
        if cooperative:
            assert ctx.wait(5)
            order.append("stopped")
            ctx.check_cancelled()
        else:
            assert release.wait(15)
            assert ctx.cancel_requested
            order.append("finished")
        return 0

    host = HostBuild(
        (),
        RuntimePlan((ComponentSpec("device", "device"),)),
        {"device": Factory()},
        (CommandContribution(("go",), command),),
    )

    def run(app):
        async def scenario():
            async with app.run_test() as pilot:
                prompt = app.query_one(PromptInput)
                prompt.value = "go private-argument"
                await pilot.press("enter")
                assert await asyncio.to_thread(started.wait, 3)
                assert app.operation_state == "running"
                prompt.value = "go second"
                await pilot.press("enter")
                assert order == ["open", "command"]
                assert "command running" in app.retained_log
                assert app._history.items == ("go",)
                await pilot.press("ctrl+f")
                assert app.query_one("#search", Input).display
                await pilot.press("ctrl+p")
                await pilot.press("escape")
                await pilot.press("ctrl+q")
                if not cooperative:
                    assert app.operation_state == "stopping"
                    assert app.return_code is None
                    assert "close" not in order
                    assert "cancelled after" not in app.retained_log
                    release.set()
                await until(pilot, lambda: app.return_code is not None)
                assert "close" not in order
            return app.return_code

        try:
            return asyncio.run(scenario())
        finally:
            release.set()

    monkeypatch.setattr("autospan.frontends.console._tui_available", lambda: True)
    monkeypatch.setattr(ConsoleApp, "run", run)
    code = run_console(host=host, evidence_root=tmp_path)
    assert code == (130 if cooperative else 0)
    assert order == ["open", "command", "stopped" if cooperative else "finished", "close"]
    assert not any(t.name == "autospan-console-command" for t in threading.enumerate())
    (manifest,) = tmp_path.glob("*/manifest.json")
    assert json.loads(manifest.read_text())["outcome"] == ("failed" if cooperative else "passed")


@pytest.mark.parametrize("focus", ["prompt", "search", "palette"])
def test_ctrl_c_stops_command_without_quitting_and_allows_next_command(focus):
    started, stopping, release = (threading.Event() for _ in range(3))
    calls = []

    def command(ctx, args):
        calls.append(args)
        if args == ("wait",):
            started.set()
            assert ctx.wait(5)
            stopping.set()
            assert release.wait(15)
            ctx.check_cancelled()
        assert not ctx.cancel_requested
        ctx.write("next command finished")

    shell = CommandShell(Runtime({}, _Evidence()), (CommandContribution(("go",), command),))

    async def scenario():
        app = ConsoleApp(shell)
        try:
            async with app.run_test() as pilot:
                prompt = app.query_one(PromptInput)
                prompt.value = "go wait"
                await pilot.press("enter")
                assert await asyncio.to_thread(started.wait, 3)
                if focus == "search":
                    await pilot.press("ctrl+f")
                elif focus == "palette":
                    await pilot.press("ctrl+space")
                await pilot.press("ctrl+c", "ctrl+c")
                assert await asyncio.to_thread(stopping.wait, 3)
                assert app.operation_state == "stopping"
                assert app.return_code is None
                assert "operation cancelled after command stopped" not in app.retained_log
                release.set()
                await until(pilot, lambda: app.operation_state == "failed")
                assert app.is_running
                assert app.status == 130
                assert "operation cancelled after command stopped" in app.retained_log
                if focus == "palette":
                    await pilot.press("escape")
                elif focus == "search":
                    await pilot.press("enter")
                assert prompt.has_focus
                prompt.value = "go next"
                await pilot.press("enter")
                await until(pilot, lambda: "next command finished" in app.retained_log)
                assert calls == [("wait",), ("next",)]
                assert app.status == 130
                await pilot.press("ctrl+q")
            assert app.return_code == 130
        finally:
            release.set()

    asyncio.run(scenario())


def test_forced_ui_exit_still_waits_for_command_and_preserves_failure(
    tmp_path, monkeypatch, capsys
):
    started = threading.Event()
    order = []

    class Factory:
        @contextmanager
        def open(self, context, dependencies, config):
            try:
                yield object()
            finally:
                order.append("closed")

    def command(ctx, args):
        started.set()
        assert ctx.wait(5)
        order.append("command stopped")
        ctx.check_cancelled()

    host = HostBuild(
        (),
        RuntimePlan((ComponentSpec("device", "device"),)),
        {"device": Factory()},
        (CommandContribution(("go",), command),),
    )

    def run(app):
        async def scenario():
            async with app.run_test() as pilot:
                app.query_one(PromptInput).value = "go"
                await pilot.press("enter")
                assert await asyncio.to_thread(started.wait, 3)
                app.exit(0)
            assert app.status == 130
            return app.return_code

        return asyncio.run(scenario())

    monkeypatch.setattr("autospan.frontends.console._tui_available", lambda: True)
    monkeypatch.setattr(ConsoleApp, "run", run)
    assert run_console(host=host, evidence_root=tmp_path) == 130
    assert "operation cancelled after command stopped" in capsys.readouterr().out
    assert order == ["command stopped", "closed"]
    (manifest,) = tmp_path.glob("*/manifest.json")
    assert json.loads(manifest.read_text())["outcome"] == "failed"


@pytest.mark.parametrize("interruption", [KeyboardInterrupt("stop"), SystemExit("stop")])
def test_interrupted_emergency_shutdown_keeps_worker_resources(tmp_path, monkeypatch, interruption):
    started, interrupted, waiting, release = (threading.Event() for _ in range(4))
    closed = threading.Event()
    errors, workers = [], []
    order = []

    class Factory:
        @contextmanager
        def open(self, context, dependencies, config):
            try:
                yield object()
            finally:
                order.append("closed")
                closed.set()

    def command(ctx, args):
        started.set()
        assert release.wait(5)
        ctx.runtime.get("device")
        order.append("command finished")

    plan = RuntimePlan(
        (ComponentSpec("device", "device", claims=(ResourceClaim("console:interrupt"),)),)
    )
    host = HostBuild((), plan, {"device": Factory()}, (CommandContribution(("go",), command),))

    def run(app):
        original_get = app._output.get

        def get(*args, **kwargs):
            if not interrupted.is_set():
                interrupted.set()
                raise interruption
            if not waiting.is_set():
                waiting.set()
                raise KeyboardInterrupt("second interrupt")
            return original_get(*args, **kwargs)

        monkeypatch.setattr(app._output, "get", get)

        def execute():
            try:
                app._outcome = app.shell.execute("go", cancellation=app._cancel_request)
            finally:
                app._completed.set()

        app._completed.clear()
        app._thread = threading.Thread(target=execute, daemon=True)
        workers.append(app._thread)
        app._thread.start()
        assert started.wait(5)
        return 0

    def console():
        try:
            run_console(host=host, evidence_root=tmp_path / "console")
        except BaseException as error:
            errors.append(error)
        finally:
            waiting.set()

    monkeypatch.setattr("autospan.frontends.console._tui_available", lambda: True)
    monkeypatch.setattr(ConsoleApp, "run", run)
    closer = threading.Thread(target=console, daemon=True)
    evidence = LocalEvidenceStore(tmp_path / "competitor")
    other = RuntimeSession(plan=plan, factories=host.factories, evidence=evidence)
    closer.start()
    try:
        assert interrupted.wait(5)
        assert waiting.wait(5)
        assert not closed.is_set()
        with pytest.raises(LifecycleError, match="already claimed"):
            with other:
                pytest.fail("claim released while command was active")
    finally:
        release.set()
        closer.join(5)
        for worker in workers:
            worker.join(5)
        evidence.close()
    assert not closer.is_alive()
    assert all(not worker.is_alive() for worker in workers)
    assert errors == [interruption]
    assert "second interrupt" in " ".join(interruption.__notes__)
    assert order == ["command finished", "closed"]
    (manifest,) = (tmp_path / "console").glob("*/manifest.json")
    assert json.loads(manifest.read_text())["outcome"] == "failed"
    # A fresh owner can acquire the claim after cleanup.
    retry_evidence = LocalEvidenceStore(tmp_path / "retry")
    try:
        with RuntimeSession(plan=plan, factories=host.factories, evidence=retry_evidence):
            pass
    finally:
        retry_evidence.close()


@pytest.mark.parametrize("key", ["ctrl+p", "ctrl+space", "ctrl+@"])
def test_command_palette_shortcuts_open_the_picker(key):
    from textual.command import CommandPalette

    async def scenario():
        app = ConsoleApp(_shell())
        async with app.run_test() as pilot:
            await pilot.press(key)
            assert isinstance(app.screen, CommandPalette)
            await pilot.press("escape")
            assert app.query_one(PromptInput).has_focus

    asyncio.run(scenario())


def test_native_palette_prefills_existing_plugin_commands():
    async def scenario():
        app = ConsoleApp(_shell())
        async with app.run_test():
            provider = ConsoleCommands(app.screen)
            hits = [hit async for hit in provider.search("syhl")]
            assert {hit.text for hit in hits} == {"say hello", "say helium"}
            hits[0].command()
            assert app.query_one(PromptInput).value.startswith("say hel")
            assert app.operation_state == "idle"
            discovered = [hit async for hit in provider.discover()]
            assert {hit.display for hit in discovered} == {
                "help",
                "components",
                "evidence",
                "exit",
                "quit",
                "say hello",
                "say helium",
            }
            builtin = [hit async for hit in provider.search("components")]
            assert any(hit.text == "components" for hit in builtin)

    asyncio.run(scenario())


def test_log_search_clipboard_export_and_unicode_bounds(tmp_path, monkeypatch):
    evidence = LocalEvidenceStore(tmp_path)
    shell = CommandShell(Runtime({}, evidence), ())
    copied = []
    monkeypatch.setattr(ConsoleApp, "copy_to_clipboard", lambda self, text: copied.append(text))

    async def scenario():
        app = ConsoleApp(shell)
        async with app.run_test() as pilot:
            for i in range(1100):
                app.record(f"雪 line {i}")
            assert len(app._log_text) == 1000
            await pilot.press("ctrl+y")
            assert len(copied) == 1 and "雪 line 1099" in copied[0]
            await pilot.press("ctrl+s")
            exported = Path(evidence.root / "console.log").read_text(encoding="utf-8")
            assert "雪 line 1099" in exported and "雪 line 0\n" not in exported
            await pilot.press("ctrl+f")
            search = app.query_one("#search", Input)
            search.value = "line 1099"
            await pilot.press("enter")
            assert any("1099" in line.text for line in app.query_one(RichLog).lines)
            assert all("1099" in line.text for line in app.query_one(RichLog).lines)

    try:
        asyncio.run(scenario())
    finally:
        evidence.close(outcome="passed")


def test_streaming_large_output_uses_bounded_queue_without_losing_failure():
    def command(ctx, args):
        for i in range(300):
            ctx.write(f"{i}: " + "雪" * 5000)
        return 11

    shell = CommandShell(Runtime({}, _Evidence()), (CommandContribution(("flood",), command),))

    async def scenario():
        app = ConsoleApp(shell)
        async with app.run_test() as pilot:
            app.query_one(PromptInput).value = "flood"
            await pilot.press("enter")
            await pilot.press("ctrl+f")
            assert app.query_one("#search", Input).display
            await until(pilot, lambda: app.operation_state == "failed", timeout=20)
            assert len(app._log_text) <= 1000
            assert app._output.maxsize == 128 and app._output.empty()
            assert "status 11" in app.retained_log
            assert app.status == 11
            await pilot.press("ctrl+q")
        assert app.return_code == 11

    asyncio.run(scenario())
