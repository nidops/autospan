"""Composition root joining selected plugins, planning, and frontends."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from autospan.api.commands import CommandContribution
from autospan.api.component import ComponentFactory, RuntimePlan
from autospan.api.errors import PlanError, PluginError
from autospan.api.frontend import FrontendContribution
from autospan.api.gates import GateContribution
from autospan._core.discovery import LoadedPlugin, load_selected_plugins
from autospan._core.planner import compile_runtime_plan
from autospan._core.providers import (
    ProviderComponentFactory,
    load_referenced_providers,
    validate_provider_components,
)
from autospan.api.provider import PROVIDER_TYPE_ID

#: Gates every host understands. Plugins add domain gates (e.g. ``flashing``).
DEFAULT_GATES: tuple[GateContribution, ...] = (
    GateContribution("hardware", "allow scenarios/components that touch physical hardware"),
    GateContribution(
        "destructive",
        "allow scenarios that may alter target state or software",
        implies=("hardware",),
    ),
)


@dataclass(frozen=True, slots=True)
class HostBuild:
    plugins: tuple[LoadedPlugin, ...]
    plan: RuntimePlan
    factories: Mapping[str, ComponentFactory]
    commands: tuple[CommandContribution, ...]
    gates: tuple[GateContribution, ...] = DEFAULT_GATES
    frontends: tuple[FrontendContribution, ...] = ()
    config: Mapping[str, Any] = field(default_factory=dict)
    config_path: Path | None = None

    def gate(self, name: str) -> GateContribution:
        for gate in self.gates:
            if gate.name == name:
                return gate
        raise KeyError(name)

    def gate_closure(self, name: str) -> tuple[str, ...]:
        """Return ``name`` plus every gate it implies, transitively."""
        ordered: list[str] = []

        def visit(current: str) -> None:
            if current in ordered:
                return
            ordered.append(current)
            for implied in self.gate(current).implies:
                visit(implied)

        visit(name)
        return tuple(ordered)

    def frontend(self, name: str) -> FrontendContribution:
        for frontend in self.frontends:
            if frontend.name == name:
                return frontend
        available = ", ".join(item.name for item in self.frontends) or "<none>"
        raise PluginError(f"Unknown frontend {name!r}; available: {available}")


def _collect_gates(plugins: tuple[LoadedPlugin, ...]) -> tuple[GateContribution, ...]:
    gates: dict[str, tuple[GateContribution, str]] = {
        gate.name: (gate, "autospan") for gate in DEFAULT_GATES
    }
    for loaded in plugins:
        for gate in loaded.registry.gates:
            if gate.name in gates:
                owner = gates[gate.name][1]
                raise PluginError(
                    f"Gate {gate.name!r} from plugin {loaded.name!r} is already defined by {owner!r}"
                )
            gates[gate.name] = (gate, loaded.name)
    for gate, owner in gates.values():
        for implied in gate.implies:
            if implied not in gates:
                raise PluginError(f"Gate {gate.name!r} ({owner}) implies unknown gate {implied!r}")
    # Reject implication cycles early; a cycle would make every gate unreachable.
    for name in gates:
        stack: list[tuple[str, tuple[str, ...]]] = [(name, (name,))]
        while stack:
            current, path = stack.pop()
            for implied in gates[current][0].implies:
                if implied == name:
                    raise PluginError(f"Gate implication cycle: {' -> '.join((*path, implied))}")
                if implied not in path:
                    stack.append((implied, (*path, implied)))
    return tuple(gate for gate, _ in gates.values())


def build_host(
    *,
    plugin_names: tuple[str, ...],
    config: Mapping[str, Any],
    config_dir: Path | None = None,
    config_path: Path | None = None,
) -> HostBuild:
    plugins = load_selected_plugins(plugin_names)
    plan = compile_runtime_plan(
        plugins=plugins,
        raw_config=config,
        config_dir=(config_dir or Path.cwd()),
    )

    factories: dict[str, ComponentFactory] = {}
    commands: dict[tuple[str, ...], CommandContribution] = {}
    frontends: dict[str, FrontendContribution] = {}
    for loaded in plugins:
        for type_id, factory in loaded.registry.component_types.items():
            if type_id in factories:
                raise PluginError(f"Duplicate component type across plugins: {type_id!r}")
            factories[type_id] = factory
        for command in loaded.registry.commands:
            if command.path in commands:
                rendered = " ".join(command.path)
                raise PluginError(f"Duplicate command path across plugins: {rendered}")
            commands[command.path] = command
        for frontend in loaded.registry.frontends:
            if frontend.name in frontends:
                raise PluginError(f"Duplicate frontend across plugins: {frontend.name!r}")
            frontends[frontend.name] = frontend

    provider_table = load_referenced_providers(plan.components)
    validate_provider_components(plan.components, provider_table)
    factories[PROVIDER_TYPE_ID] = ProviderComponentFactory(provider_table)
    gates = _collect_gates(plugins)
    known_gates = {gate.name for gate in gates}
    for spec in plan.components:
        unknown = [gate for gate in spec.gates if gate not in known_gates]
        if unknown:
            raise PlanError(
                f"Component {spec.name!r} requires unknown gate(s): {', '.join(unknown)}"
            )

    return HostBuild(
        plugins=plugins,
        plan=plan,
        factories=factories,
        commands=tuple(commands[path] for path in sorted(commands)),
        gates=gates,
        frontends=tuple(frontends[name] for name in sorted(frontends)),
        config=config,
        config_path=config_path,
    )
