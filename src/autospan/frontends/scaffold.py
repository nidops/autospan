"""``autospan new``: scaffold a project plugin that follows the standard pattern."""

from __future__ import annotations

import re
from pathlib import Path

from autospan.api.errors import AutoSpanError

_NAME = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")

_PYPROJECT = """\
[build-system]
requires = ["setuptools>=75", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "autospan-__NAME__"
version = "0.1.0"
description = "AutoSpan project plugin: __NAME__"
requires-python = ">=3.11"
dependencies = ["autospan>=0.2,<0.3"]

[project.optional-dependencies]
dev = ["pytest>=8,<10"]

[project.entry-points."autospan.plugins"]
__NAME__ = "__PKG__.plugin:PLUGIN"

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
__PKG__ = ["py.typed", "*.yaml"]

[tool.pytest.ini_options]
testpaths = ["tests"]
"""

_CONFIG = """\
# autospan test --config autospan.yaml     |     autospan console --config autospan.yaml
autospan:
  plugins:
    - __NAME__

__NAME__:
  name: bench-1
"""

_README = """\
# AutoSpan project: __NAME__

This generated plugin uses a simulated rig and needs no hardware. From this
directory, with AutoSpan installed in an active Python 3.11+ environment:

```bash
python -m pip install -e '.[dev]'
autospan plan --config autospan.yaml
autospan test --config autospan.yaml -- -q
autospan console --config autospan.yaml --plain --exec '__NAME__ status'
python -m pytest -q tests
```

`src/__PKG__/plugin.py` declares plugin API 1, configuration, a component factory
and a command. Add drivers inside the factory's `ExitStack`, validate your project
configuration, and gate physical components with `gates=("hardware",)`.
Put executable scenarios in `src/__PKG__/scenarios/` and plugin unit tests in `tests/`.
Use one allocation scope per bench: `runtime` acquires per scenario;
`session_runtime` shares one acquisition for the pytest session.

Interactive `autospan console --config autospan.yaml` uses Textual when the
`autospan[tui]` extra is installed and an interactive TTY is available.
See the [plugin guide](https://github.com/nidops/autospan/blob/master/docs/PLUGIN_AUTHORING.md).
"""

_PLUGIN = '''\
"""Project plugin ``__NAME__``.

Pattern: one plugin per project. It owns its configuration, opens the drivers it
needs in one component, and contributes scenarios and console commands.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import Any

from autospan.api import (
    AutoSpanPlugin,
    CommandContribution,
    CommandContext,
    ComponentContext,
    ComponentSpec,
    PlanBuilder,
    PluginDescriptor,
    PluginRegistrar,
    ResourceClaim,
    ScenarioContribution,
)

TYPE_ID = "__NAME__.rig"
COMPONENT = "__NAME__.rig"


@dataclass(frozen=True)
class Rig:
    """What this project's scenarios receive. Add your driver handles here."""

    name: str


class _RigFactory:
    @contextmanager
    def open(
        self, context: ComponentContext, dependencies: Mapping[str, object], config: Mapping[str, Any]
    ) -> Iterator[Rig]:
        del context, dependencies
        with ExitStack() as stack:
            # Open drivers here with stack.enter_context(...); they close in reverse order.
            del stack
            yield Rig(str(config["name"]))


def _compile(config: Mapping[str, Any], plan: PlanBuilder) -> None:
    name = str(config.get("name", "bench-1"))
    plan.add_component(
        ComponentSpec(
            name=COMPONENT,
            type_id=TYPE_ID,
            claims=(ResourceClaim(f"__NAME__:{name}"),),
            config={"name": name},
        )
    )


def _status(ctx: CommandContext, args: tuple[str, ...]) -> int:
    del args
    ctx.write(f"rig: {ctx.runtime.require(COMPONENT, Rig).name}")
    return 0


@dataclass(frozen=True)
class _Plugin(AutoSpanPlugin):
    descriptor: PluginDescriptor = PluginDescriptor(
        name="__NAME__", version="0.1.0", api_version=1
    )

    def register(self, registrar: PluginRegistrar) -> None:
        registrar.component_type(TYPE_ID, _RigFactory())
        registrar.plan_compiler(_compile)
        registrar.scenarios(ScenarioContribution(package="__PKG__"))
        registrar.command(CommandContribution(("__NAME__", "status"), _status, "show the rig"))


PLUGIN = _Plugin()
'''

_SCENARIO = '''\
from __future__ import annotations

from autospan.api import Runtime

from __PKG__.plugin import COMPONENT, Rig


def test_rig_is_available(runtime: Runtime) -> None:
    """The configured rig shall be live for every scenario."""
    with runtime.evidence.step("Check rig"):
        rig = runtime.require(COMPONENT, Rig)
    assert rig.name == "bench-1"
'''

_PLUGIN_TEST = """\
from __future__ import annotations

from autospan.testing import compile_plugin

from __PKG__.plugin import COMPONENT, PLUGIN


def test_plugin_plans_one_rig(tmp_path) -> None:
    builder = compile_plugin(PLUGIN, {"name": "bench-1"}, config_dir=tmp_path)
    assert [spec.name for spec in builder.components] == [COMPONENT]
"""


def new_project(name: str, parent: Path) -> Path:
    """Create a runnable project and return its path; reject invalid/existing names."""
    if not _NAME.match(name):
        raise AutoSpanError(f"Project name {name!r} must be lowercase words joined by '-'")
    pkg = name.replace("-", "_")
    root = parent / f"autospan-{name}"
    if root.exists():
        raise AutoSpanError(f"{root} already exists")

    def render(text: str) -> str:
        return text.replace("__NAME__", name).replace("__PKG__", pkg)

    files = {
        "README.md": _README,
        "pyproject.toml": _PYPROJECT,
        "autospan.yaml": _CONFIG,
        f"src/{pkg}/__init__.py": "",
        f"src/{pkg}/py.typed": "",
        f"src/{pkg}/plugin.py": _PLUGIN,
        f"src/{pkg}/scenarios/__init__.py": "",
        f"src/{pkg}/scenarios/test_smoke.py": _SCENARIO,
        "tests/test_plugin.py": _PLUGIN_TEST,
    }
    for relative, text in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render(text), encoding="utf-8", newline="\n")
    return root
