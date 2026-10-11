from __future__ import annotations

import os
import subprocess
import sys

import pytest
from autospan.api.errors import AutoSpanError
from autospan.frontends.cli import main
from autospan.frontends.scaffold import new_project


def test_new_creates_the_standard_layout(tmp_path, capsys) -> None:
    assert main(["new", "acme-bench", "--dir", str(tmp_path)]) == 0
    root = tmp_path / "autospan-acme-bench"
    for relative in (
        "README.md",
        "pyproject.toml",
        "autospan.yaml",
        "src/acme_bench/plugin.py",
        "src/acme_bench/scenarios/test_smoke.py",
        "tests/test_plugin.py",
    ):
        assert (root / relative).is_file(), relative
    assert "acme-bench" in (root / "autospan.yaml").read_text()
    assert "__NAME__" not in (root / "src/acme_bench/plugin.py").read_text()
    readme = (root / "README.md").read_text()
    assert "acme-bench status" in readme
    assert "src/acme_bench/plugin.py" in readme
    assert "__NAME__" not in readme and "__PKG__" not in readme


def test_new_rejects_bad_names_and_existing_dirs(tmp_path) -> None:
    with pytest.raises(AutoSpanError):
        new_project("Bad Name", tmp_path)
    new_project("ok", tmp_path)
    with pytest.raises(AutoSpanError):
        new_project("ok", tmp_path)


def test_generated_plugin_tests_pass(tmp_path) -> None:
    root = new_project("acme-bench", tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
        cwd=root,
        env={
            **os.environ,
            # Expose only the uninstalled generated plugin. Core must come from
            # the active environment, including during wheel/sdist validation.
            "PYTHONPATH": str(root / "src"),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
