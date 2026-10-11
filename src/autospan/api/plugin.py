"""Stable project-plugin registration surface."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from autospan.api.commands import CommandContribution
from autospan.api.component import ComponentFactory, ComponentSpec, ScenarioContribution
from autospan.api.frontend import FrontendContribution
from autospan.api.gates import GateContribution

#: Plugin API generation implemented by this host. Bumped only on a breaking
#: change to the project-plugin contract.
AUTOSPAN_PLUGIN_API_VERSION = 1
SUPPORTED_PLUGIN_API_VERSIONS = frozenset({AUTOSPAN_PLUGIN_API_VERSION})
PLUGIN_ENTRY_POINT_GROUP = "autospan.plugins"


@dataclass(frozen=True, slots=True)
class PluginDescriptor:
    """Identity and host-API compatibility for one project plugin."""

    name: str
    version: str
    api_version: int

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("PluginDescriptor.name must not be empty")


class PlanBuilder(Protocol):
    """Side-effect-free planning context passed to the selected project plugin."""

    @property
    def config_dir(self) -> Path:
        """Directory containing the host config; resolve project-relative paths here."""
        ...

    def add_component(self, spec: ComponentSpec) -> None:
        """Declare one configured runtime component without opening it."""
        ...

    def add_metadata(self, key: str, value: object) -> None:
        """Attach diagnostic planning metadata under a unique key."""
        ...


PlanCompiler = Callable[[Mapping[str, Any], PlanBuilder], None]


class PluginRegistrar(Protocol):
    """Declaration-only registration API supplied to a project plugin."""

    def component_type(self, type_id: str, factory: ComponentFactory) -> None:
        """Register a project component factory under a globally unique type identifier."""
        ...

    def plan_compiler(self, compiler: PlanCompiler) -> None:
        """Register a side-effect-free compiler from project config to component specs."""
        ...

    def scenarios(self, contribution: ScenarioContribution) -> None:
        """Contribute an installed scenario package for framework-owned pytest collection."""
        ...

    def command(self, contribution: CommandContribution) -> None:
        """Contribute one console command operating on the shared runtime."""
        ...

    def gate(self, contribution: GateContribution) -> None:
        """Contribute one explicit execution gate (``--NAME`` flag + ``NAME`` marker)."""
        ...

    def frontend(self, contribution: FrontendContribution) -> None:
        """Contribute one frontend runnable through ``autospan run NAME``."""
        ...


class AutoSpanPlugin(Protocol):
    """Minimal contract for one installed project plugin."""

    descriptor: PluginDescriptor

    def register(self, registrar: PluginRegistrar) -> None:
        """Declare project components, scenarios, commands, gates and frontends."""
        ...
