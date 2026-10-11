"""Verify the core imports and distribution stay project- and vendor-independent."""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

KERNEL_ROOT = Path(__file__).resolve().parents[1]
SOURCE = KERNEL_ROOT / "src" / "autospan"

# Explicit framework integrations, not project drivers or vendor adapters.
ALLOWED_EXTERNAL_IMPORTS = {"yaml", "pytest", "textual"}
ALLOWED_DISTRIBUTIONS = {
    "setuptools",
    "wheel",
    "pyyaml",
    "pytest",
    "textual",
    "ruff",
    "mypy",
    "types-pyyaml",
    "build",
    "twine",
    "packaging",
    "readme-renderer",
}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
        elif (
            isinstance(node, ast.Call)
            and getattr(node.func, "attr", getattr(node.func, "id", None)) == "import_module"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            found.add(node.args[0].value.split(".")[0])
    return found


def test_kernel_source_imports_only_framework_integrations() -> None:
    allowed = set(sys.stdlib_module_names) | {"autospan", "__future__"} | ALLOWED_EXTERNAL_IMPORTS
    offenders = {
        str(path.relative_to(KERNEL_ROOT)): sorted(_imported_modules(path) - allowed)
        for path in SOURCE.rglob("*.py")
    }
    assert {name: mods for name, mods in offenders.items() if mods} == {}


def test_distribution_declares_only_reviewed_dependencies() -> None:
    metadata = tomllib.loads((KERNEL_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = metadata["project"]
    requirements = [*metadata["build-system"]["requires"], *project.get("dependencies", [])]
    for extra in project.get("optional-dependencies", {}).values():
        requirements.extend(extra)
    names = {
        re.sub(r"[-_.]+", "-", re.split(r"[<>=!~;\[\s@]", item, maxsplit=1)[0]).lower()
        for item in requirements
    }
    assert names <= ALLOWED_DISTRIBUTIONS, sorted(names - ALLOWED_DISTRIBUTIONS)
