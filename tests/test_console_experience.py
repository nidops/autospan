"""Headless operator workflow regressions for the Textual console."""

from __future__ import annotations

import asyncio
import threading

import pytest

pytest.importorskip("textual")

from autospan.api import CommandContribution, CommandShell, Runtime  # noqa: E402
from autospan.frontends.tui.app import ConsoleApp, PromptInput  # noqa: E402
from textual.widgets import Input  # noqa: E402


class _Evidence:
    root = "/tmp/autospan-console-test"


async def _until(pilot, predicate) -> None:
    async with asyncio.timeout(5):
        while not predicate():
            await pilot.pause()
            await asyncio.sleep(0.01)


def test_busy_submit_keeps_user_draft_until_explicit_resubmit() -> None:
    entered, release = threading.Event(), threading.Event()
    called: list[tuple[str, ...]] = []

    def work(ctx, args):
        called.append(args)
        if args == ("slow",):
            entered.set()
            assert release.wait(5)

    shell = CommandShell(
        Runtime({}, _Evidence()),
        (CommandContribution(("work",), work),),
    )

    async def scenario():
        app = ConsoleApp(shell)
        try:
            async with app.run_test() as pilot:
                prompt = app.query_one(PromptInput)
                prompt.value = "work slow"
                await pilot.press("enter")
                assert await asyncio.to_thread(entered.wait, 3)
                prompt.value = "work later"
                await pilot.press("enter")
                assert prompt.value == "work later"
                assert called == [("slow",)]
                release.set()
                await _until(pilot, lambda: app.operation_state == "idle")
                assert called == [("slow",)]  # Never auto-queue actions against hardware.
                await pilot.press("enter")
                await _until(pilot, lambda: called == [("slow",), ("later",)])
                await _until(pilot, lambda: app.operation_state == "idle")
                assert prompt.value == ""
        finally:
            release.set()

    asyncio.run(scenario())


def test_idle_ctrl_c_clears_draft_and_resets_history_without_logging_input() -> None:
    async def scenario():
        app = ConsoleApp(CommandShell(Runtime({}, _Evidence()), ()))
        async with app.run_test() as pilot:
            app._prompt.value = "help"
            await pilot.press("enter")
            await _until(pilot, lambda: app._thread is None)
            app._prompt.value = "private discarded draft"
            await pilot.press("up", "ctrl+c")
            assert app._prompt.value == "" and app._prompt.has_focus
            assert app._history.items == ("help",)
            assert app.retained_log.endswith("^C")
            assert "private discarded draft" not in app.retained_log
            await pilot.press("up")
            assert app._prompt.value == "help"
            await pilot.press("down")
            assert app._prompt.value == ""
            await pilot.press("ctrl+c")
            assert app.is_running and app._prompt.value == ""

    asyncio.run(scenario())


@pytest.mark.parametrize("apply_search", [False, True])
def test_idle_ctrl_c_closes_search_and_discards_command_draft(apply_search) -> None:
    async def scenario():
        app = ConsoleApp(CommandShell(Runtime({}, _Evidence()), ()))
        async with app.run_test() as pilot:
            app.record("wanted output")
            app.record("other output")
            app._prompt.value = "private discarded draft"
            await pilot.press("ctrl+f")
            app._search.value = "wanted"
            if apply_search:
                await pilot.press("enter")
                assert not any("other output" in line.text for line in app._log_view.lines)
            await pilot.press("ctrl+c")
            assert not app._search.display and app._search.value == ""
            assert not app._filter
            assert app._prompt.value == "" and app._prompt.has_focus
            assert any("other output" in line.text for line in app._log_view.lines)
            assert app.retained_log.endswith("^C")
            assert "private discarded draft" not in app.retained_log

    asyncio.run(scenario())


def test_idle_ctrl_c_leaves_palette_and_underlying_search_untouched() -> None:
    from textual.command import CommandPalette

    async def scenario():
        app = ConsoleApp(CommandShell(Runtime({}, _Evidence()), ()))
        async with app.run_test() as pilot:
            app._prompt.value = "preserved draft"
            await pilot.press("ctrl+f")
            app._search.value = "wanted"
            await pilot.press("enter", "ctrl+p")
            palette = app.screen
            assert isinstance(palette, CommandPalette)
            before = app.retained_log
            await pilot.press("ctrl+c")
            assert app.screen is palette
            assert app._prompt.value == "preserved draft"
            assert app._search.display and app._filter == "wanted"
            assert app.retained_log == before
            await pilot.press("escape")

    asyncio.run(scenario())


@pytest.mark.parametrize("open_palette", [False, True])
def test_running_ctrl_c_cancels_worker_and_preserves_draft(open_palette) -> None:
    entered, release = threading.Event(), threading.Event()

    def work(ctx, args):
        entered.set()
        assert release.wait(5)
        ctx.check_cancelled()

    shell = CommandShell(Runtime({}, _Evidence()), (CommandContribution(("work",), work),))

    async def scenario():
        app = ConsoleApp(shell)
        async with app.run_test() as pilot:
            try:
                app._prompt.value = "work"
                await pilot.press("enter")
                await _until(pilot, entered.is_set)
                app._prompt.value = "preserved draft"
                if open_palette:
                    await pilot.press("ctrl+p")
                screen = app.screen
                await pilot.press("ctrl+c")
                assert app._cancel_request.is_set()
                assert app._prompt.value == "preserved draft"
                assert app.screen is screen
                release.set()
                await _until(pilot, lambda: app._thread is None)
                assert app._last_status == 130
                assert app._prompt.value == "preserved draft"
                if open_palette:
                    await pilot.press("escape")
            finally:
                release.set()

    asyncio.run(scenario())


def test_ctrl_c_preserves_draft_while_completed_worker_output_drains(monkeypatch) -> None:
    def work(ctx, args):
        ctx.write("final output")

    shell = CommandShell(Runtime({}, _Evidence()), (CommandContribution(("work",), work),))

    async def scenario():
        app = ConsoleApp(shell)
        drain = app._drain_output
        monkeypatch.setattr(app, "_drain_output", lambda: None)
        async with app.run_test() as pilot:
            app._prompt.value = "work"
            await pilot.press("enter")
            await _until(pilot, app._completed.is_set)
            assert app._thread is not None and not app._output.empty()
            app._prompt.value = "preserved draft"
            before = app.retained_log
            await pilot.press("ctrl+c")
            assert app._prompt.value == "preserved draft"
            assert app.retained_log == before
            assert not app._cancel_request.is_set()
            drain()
            assert app._thread is None and app._last_status == 0
            assert "final output" in app.retained_log
            assert app._prompt.value == "preserved draft"

    asyncio.run(scenario())


def test_narrow_layout_sidebar_toggle_and_search_escape() -> None:
    shell = CommandShell(Runtime({}, _Evidence()), ())

    async def scenario():
        app = ConsoleApp(shell)
        async with app.run_test(size=(70, 24)) as pilot:
            assert not app._side.display
            await pilot.press("ctrl+b")
            assert app._side.display
            await pilot.press("ctrl+f")
            search = app.query_one("#search", Input)
            assert search.display and search.has_focus
            await pilot.press("escape")
            assert not search.display
            assert app.query_one(PromptInput).has_focus
            await pilot.press("ctrl+g")
            assert app.query_one(PromptInput).has_focus

    asyncio.run(scenario())


def test_large_unicode_output_retains_correct_line_boundaries() -> None:
    def big(ctx, args):
        ctx.write("a" * 2047 + "雪" * 300)
        ctx.write("second")

    shell = CommandShell(Runtime({}, _Evidence()), (CommandContribution(("big",), big),))

    async def scenario():
        app = ConsoleApp(shell)
        async with app.run_test() as pilot:
            prompt = app.query_one(PromptInput)
            prompt.value = "big"
            await pilot.press("enter")
            await _until(pilot, lambda: app.operation_state == "idle")
            assert ("a" * 2047 + "雪" * 300) in app._log_text
            assert "second" in app._log_text
            assert app._last_duration is not None

    asyncio.run(scenario())


def test_sidebar_resize_crosses_threshold_and_preserves_manual_override() -> None:
    async def scenario():
        app = ConsoleApp(CommandShell(Runtime({}, _Evidence()), ()))
        async with app.run_test(size=(80, 24)) as pilot:
            assert not app._side.display
            for width, visible in [(90, True), (89, False), (110, True), (70, False)]:
                await pilot.resize_terminal(width, 24)
                assert app._side.display is visible
            await pilot.press("ctrl+b")
            await pilot.resize_terminal(60, 24)
            assert app._side.display
            await pilot.press("ctrl+b")
            await pilot.resize_terminal(120, 24)
            assert not app._side.display

    asyncio.run(scenario())


@pytest.mark.parametrize("dismiss", ["escape", "ctrl+g", "reopen_escape"])
def test_dismissing_applied_search_restores_live_failures(dismiss) -> None:
    shell = CommandShell(
        Runtime({}, _Evidence()), (CommandContribution(("fail",), lambda ctx, args: 7),)
    )

    async def scenario():
        app = ConsoleApp(shell)
        async with app.run_test() as pilot:
            app.record("wanted earlier output")
            for index in range(100):
                app.record(f"other output {index}")
            await pilot.press("ctrl+f")
            app._search.value = "wanted"
            await pilot.press("enter")
            prompt = app.query_one(PromptInput)
            assert prompt.has_focus
            prompt.value = "fail"
            await pilot.press("enter")
            await _until(pilot, lambda: app.operation_state == "failed")
            assert "status 7" in app.retained_log
            assert not any("status 7" in line.text for line in app._log_view.lines)
            prompt.value = "draft to preserve"
            if dismiss == "reopen_escape":
                await pilot.press("ctrl+f", "escape")
            else:
                await pilot.press(dismiss)
            assert not app._search.display
            assert prompt.has_focus and prompt.value == "draft to preserve"
            assert any("status 7" in line.text for line in app._log_view.lines)
            app.record("new live output")
            await pilot.pause()
            assert any("new live output" in line.text for line in app._log_view.lines)
            assert app.status == 7
            if dismiss == "ctrl+g":
                assert app._log_view.max_scroll_y > 0
                assert app._log_view.scroll_y == app._log_view.max_scroll_y

    asyncio.run(scenario())


def test_palette_escape_does_not_dismiss_underlying_log_search() -> None:
    from textual.command import CommandPalette

    async def scenario():
        app = ConsoleApp(CommandShell(Runtime({}, _Evidence()), ()))
        async with app.run_test() as pilot:
            app.record("wanted output")
            app.record("hidden output")
            await pilot.press("ctrl+f")
            app._search.value = "wanted"
            await pilot.press("enter", "ctrl+p")
            assert isinstance(app.screen, CommandPalette)
            await pilot.press("escape")
            assert not isinstance(app.screen, CommandPalette)
            assert app._search.display
            assert not any("hidden output" in line.text for line in app._log_view.lines)
            await pilot.press("escape")
            assert not app._search.display
            assert any("hidden output" in line.text for line in app._log_view.lines)

    asyncio.run(scenario())


def test_command_duration_stops_when_worker_finishes_before_output_drains(monkeypatch) -> None:
    from autospan.frontends.tui import app as app_module

    # Replace only this module's clock; Textual's own timers use real time.
    class Clock:
        now = 10.0

        @classmethod
        def monotonic(cls):
            return cls.now

    def work(ctx, args):
        ctx.write("completed output")
        Clock.now = 12.0

    shell = CommandShell(Runtime({}, _Evidence()), (CommandContribution(("work",), work),))
    monkeypatch.setattr(app_module, "time", Clock)

    async def scenario():
        app = ConsoleApp(shell)
        drain = app._drain_output
        monkeypatch.setattr(app, "_drain_output", lambda: None)
        async with app.run_test() as pilot:
            app._prompt.value = "work"
            await pilot.press("enter")
            await _until(pilot, app._completed.is_set)
            assert not app._output.empty()
            Clock.now = 100.0
            app._update_status()
            assert "Elapsed: 2.0s" in app._status_text
            drain()
            assert app._last_duration == 2.0
            assert "completed output" in app.retained_log

    asyncio.run(scenario())
