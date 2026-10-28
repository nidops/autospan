"""The no-config Lab launcher preserves shell status and output lifetime."""

import subprocess
import sys

import pytest

from autospan.demos import __main__ as launcher


@pytest.mark.parametrize("developer", [False, True])
def test_check_does_not_load_tui_or_plugins(developer):
    # An isolated process also detects accidental eager Textual imports.
    script = """
import importlib.abc
import sys
class BlockPluginsAndTui(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('textual', 'autospan._core.discovery', 'autospan._core.provider_discovery')):
            raise AssertionError('Lab tried to import ' + fullname)
sys.meta_path.insert(0, BlockPluginsAndTui())
from autospan.demos.__main__ import main
raise SystemExit(main(sys.argv[1:]))
"""
    command = [sys.executable, "-c", script, "--check"]
    if developer:
        command.append("--developer")
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "3 simulated devices" in result.stdout


def test_module_entry_point_and_nonzero_status():
    result = subprocess.run(
        [sys.executable, "-m", "autospan.demos", "--exec", "lab select missing"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2 and "Select one device" in result.stdout


def test_temporary_and_retained_output_directories(tmp_path, monkeypatch, capsys):
    roots = []
    original = launcher.make_shell

    def make_shell(root, **kwargs):
        roots.append(root)
        return original(root, **kwargs)

    monkeypatch.setattr(launcher, "make_shell", make_shell)
    assert launcher.main(["--exec", "lab status"]) == 0
    assert not roots[-1].exists()
    retained = tmp_path / "exports"
    assert launcher.main(["--keep-output", str(retained), "--exec", "lab status"]) == 0
    assert retained.is_dir() and not list(retained.iterdir())
    invalid = tmp_path / "file"
    invalid.write_text("existing")
    assert launcher.main(["--keep-output", str(invalid), "--exec", "lab status"]) == 2
    assert "AutoSpan Lab:" in capsys.readouterr().err
    assert invalid.read_text() == "existing"


def test_plain_repl_preserves_shell_exit_and_highest_failure_status(tmp_path, monkeypatch):
    shell = launcher.make_shell(tmp_path)
    # Idle interrupt keeps the session; shell parses quoted exit consistently.
    lines = iter([KeyboardInterrupt(), "lab run failure", "unknown", "lab status", "'exit'"])

    def read(prompt):
        value = next(lines)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr("builtins.input", read)
    from autospan.api import CommandContext

    monkeypatch.setattr(CommandContext, "wait", lambda self, seconds: False)
    assert launcher._plain_repl(shell) == shell.status == 2


def test_plain_repl_eof_exits_successfully(tmp_path, monkeypatch):
    def eof(prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert launcher._plain_repl(launcher.make_shell(tmp_path)) == 0
