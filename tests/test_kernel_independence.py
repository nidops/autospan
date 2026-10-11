"""The kernel must stay project-, product- and vendor-tool-agnostic.

Project plugins (e.g. the private PnD plugin) and their bench tools (vFlash,
TRACE32/Lauterbach, ...) depend on AutoSpan -- never the other way round.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

KERNEL_ROOT = Path(__file__).resolve().parents[1]
SOURCE = KERNEL_ROOT / "src" / "autospan"

FORBIDDEN_TOP_LEVEL = {
    "smk_testkit",
    "autospan_pnd",
    "vflash_automation",
    "lauterbach_automation",
    "lauterbach",
    "trace32",
}
FORBIDDEN_DISTRIBUTIONS = ("smk-testkit", "autospan-pnd", "vflash", "lauterbach")


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


def test_kernel_source_imports_no_project_or_vendor_tool_package() -> None:
    offenders = {
        str(path.relative_to(KERNEL_ROOT)): sorted(_imported_modules(path) & FORBIDDEN_TOP_LEVEL)
        for path in SOURCE.rglob("*.py")
    }
    assert {name: mods for name, mods in offenders.items() if mods} == {}


def test_kernel_distribution_declares_no_project_or_vendor_tool_dependency() -> None:
    project = tomllib.loads((KERNEL_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    requirements = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        requirements.extend(extra)
    lowered = [item.lower() for item in requirements]
    assert [item for item in lowered if any(name in item for name in FORBIDDEN_DISTRIBUTIONS)] == []


FORBIDDEN_WORDS = (
    "pnd",
    "vflash",
    "lauterbach",
    "trace32",
    "canoe",
    "smk",
    "autospan_can",
    "autospan_isotp",
    "autospan_uds",
)


def test_kernel_text_names_no_project_vendor_tool_or_driver() -> None:
    this = Path(__file__).resolve()
    files = [
        *SOURCE.rglob("*.py"),
        *(KERNEL_ROOT / "docs").glob("*.md"),
        *(KERNEL_ROOT / "tests").glob("*.py"),
        KERNEL_ROOT / "README.md",
    ]
    hits = {}
    for path in files:
        if path.resolve() == this:
            continue
        text = path.read_text(encoding="utf-8").lower()
        found = [word for word in FORBIDDEN_WORDS if word in text]
        if found:
            hits[str(path.relative_to(KERNEL_ROOT))] = found
    assert hits == {}
