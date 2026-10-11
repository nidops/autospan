"""Contextual suggestions must insert safely without dispatching commands."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from autospan.api import CommandContribution, CommandShell, Runtime  # noqa: E402
from autospan.demos.evidence import SimulationEvidence  # noqa: E402
from autospan.demos.lab import make_shell  # noqa: E402
from autospan.frontends.tui.app import ConsoleApp  # noqa: E402
from textual.command import CommandPalette  # noqa: E402


@pytest.mark.parametrize("key", ["ctrl+space", "ctrl+@"])
def test_suggestion_shortcuts_and_exact_path_children(tmp_path, key):
    async def scenario():
        app = ConsoleApp(make_shell(tmp_path))
        async with app.run_test() as pilot:
            await pilot.press(key)
            assert app.suggestions_open
            assert not isinstance(app.screen, CommandPalette)
            assert "help" in app._suggestion_snapshot[3].choices
            await pilot.press("escape")
            app._prompt._set("lab select")
            await pilot.press(key)
            assert app._suggestion_snapshot[3].choices == ("controller", "power", "sensor-a")
            await pilot.press("down", "enter")
            assert app._prompt.value == "lab select power "
            assert not app.suggestions_open
            assert app._thread is None and not app._history.items
            assert "selected=sensor-a" in app.shell.execute("lab status", capture=True).output

    asyncio.run(scenario())


def test_mouse_selection_inserts_without_running(tmp_path):
    async def scenario():
        app = ConsoleApp(make_shell(tmp_path))
        async with app.run_test() as pilot:
            app._prompt._set("lab select ")
            await pilot.press("ctrl+space")
            assert await pilot.click("#suggestions", offset=(2, 2))
            await pilot.pause()
            assert not app.suggestions_open
            assert app._prompt.value == "lab select power "
            assert app._prompt.has_focus and app._thread is None
            assert not app._history.items

    asyncio.run(scenario())


@pytest.mark.parametrize("key", ["left", "ctrl+left", "home"])
def test_cursor_movement_dismisses_stale_candidates(tmp_path, key):
    async def scenario():
        app = ConsoleApp(make_shell(tmp_path))
        async with app.run_test() as pilot:
            app._prompt._set("lab select ")
            await pilot.press("ctrl+space", key)
            assert not app.suggestions_open
            assert app._prompt.value == "lab select "

    asyncio.run(scenario())


def test_typing_refreshes_suggestions_and_tab_accepts(tmp_path):
    async def scenario():
        app = ConsoleApp(make_shell(tmp_path))
        async with app.run_test() as pilot:
            app._prompt._set("lab select ")
            await pilot.press("ctrl+space", "s")
            assert app.suggestions_open
            assert app._suggestion_snapshot[3].choices == ("sensor-a",)
            await pilot.press("tab")
            assert app._prompt.value == "lab select sensor-a "
            assert not app.suggestions_open and app._thread is None

    asyncio.run(scenario())


def test_quoted_suggestion_preserves_suffix(tmp_path):
    async def scenario():
        app = ConsoleApp(make_shell(tmp_path))
        async with app.run_test() as pilot:
            app._prompt._set('lab set sample_rate "2" tail')
            app._prompt.cursor_position = len('lab set sample_rate "2')
            await pilot.press("ctrl+space", "enter")
            assert app._prompt.value == 'lab set sample_rate "20 Hz" tail'
            assert app._prompt.cursor_position == len('lab set sample_rate "20 Hz"')
            assert app._thread is None

    asyncio.run(scenario())


@pytest.mark.parametrize("target", ["search", "palette"])
def test_focus_change_closes_suggestions_without_hijacking_other_input(tmp_path, target):
    async def scenario():
        app = ConsoleApp(make_shell(tmp_path))
        async with app.run_test() as pilot:
            app._prompt._set("lab select ")
            await pilot.press("ctrl+space", "ctrl+f" if target == "search" else "ctrl+p")
            assert not app.suggestions_open
            await pilot.press("ctrl+space")
            assert not app.suggestions_open
            assert app._prompt.value == "lab select "
            if target == "search":
                assert app._search.has_focus
            else:
                assert isinstance(app.screen, CommandPalette)
            await pilot.press("escape")
            assert app._prompt.has_focus

    asyncio.run(scenario())


def test_idle_cancel_dismisses_suggestions_and_search(tmp_path):
    async def scenario():
        app = ConsoleApp(make_shell(tmp_path))
        async with app.run_test() as pilot:
            await pilot.press("ctrl+f")
            app._search.value = "needle"
            await pilot.press("enter")
            app._prompt._set("lab select ")
            await pilot.press("ctrl+space", "ctrl+c")
            assert not app.suggestions_open and not app._search.display
            assert not app._filter and app._prompt.value == ""
            assert app.retained_log.endswith("^C") and not app._history.items

    asyncio.run(scenario())


def test_render_limit_and_navigation_do_not_change_history(tmp_path):
    command = CommandContribution(
        ("choose",),
        lambda ctx, args: 0,
        complete=lambda runtime, args: tuple(f"item-{i:03}" for i in range(100)),
    )

    async def scenario():
        app = ConsoleApp(CommandShell(Runtime({}, SimulationEvidence(tmp_path)), (command,)))
        async with app.run_test() as pilot:
            app._history.add("help")
            app._prompt._set("choose ")
            await pilot.press("ctrl+space")
            assert app._suggestions.option_count == 50
            await pilot.press("up")
            assert app._suggestions.highlighted == 49
            await pilot.press("down", "shift+tab", "enter")
            assert app._prompt.value == "choose item-049 "
            assert app._history.items == ("help",) and app._thread is None

    asyncio.run(scenario())


def test_completion_failure_is_reported_without_dispatch(tmp_path):
    def complete(runtime, args):
        raise ValueError("broken completer")

    command = CommandContribution(("choose",), lambda ctx, args: 0, complete=complete)

    async def scenario():
        app = ConsoleApp(CommandShell(Runtime({}, SimulationEvidence(tmp_path)), (command,)))
        async with app.run_test() as pilot:
            app._prompt._set("choose ")
            await pilot.press("ctrl+space")
            assert not app.suggestions_open and app._thread is None
            assert "completion failed: ValueError: broken completer" in app.retained_log

    asyncio.run(scenario())
