"""Compile selected plugin configuration into an immutable RuntimePlan."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autospan.api.component import ComponentSpec, RuntimePlan, ScenarioContribution
from autospan.api.errors import AutoSpanError, ConfigurationError, PlanError
from autospan.api.provider import PROVIDER_TYPE_ID
from autospan._core.discovery import LoadedPlugin
from autospan._core.graph import topological_order
from autospan._core.resource_claims import validate_resource_claims


@dataclass(slots=True)
class MutablePlanBuilder:
    """Mutable declaration sink used only while compiling a RuntimePlan."""

    config_dir: Path
    components: list[ComponentSpec] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)

    def add_component(self, spec: ComponentSpec) -> None:
        """Append one component declaration to the plan under construction."""
        self.components.append(spec)

    def add_metadata(self, key: str, value: object) -> None:
        """Attach unique plugin/plan metadata."""
        if key in self.metadata:
            raise PlanError(f"Duplicate RuntimePlan metadata key: {key!r}")
        self.metadata[key] = value


def compile_runtime_plan(
    *,
    plugins: tuple[LoadedPlugin, ...],
    raw_config: Mapping[str, Any],
    config_dir: Path,
) -> RuntimePlan:
    """Compile and validate selected plugins without touching hardware.

    Configuration-shaped ``KeyError``/``ValueError``/``TypeError`` failures from
    plugin compilers are normalized into :class:`ConfigurationError` with the
    owning plugin named. AutoSpan exceptions are preserved unchanged.
    """

    builder = MutablePlanBuilder(config_dir=config_dir.resolve())
    scenarios: list[ScenarioContribution] = []
    known_component_types: set[str] = {PROVIDER_TYPE_ID}

    for loaded in plugins:
        overlap = known_component_types.intersection(loaded.registry.component_types)
        if overlap:
            names = ", ".join(sorted(overlap))
            raise PlanError(f"Component type collision across selected plugins: {names}")
        known_component_types.update(loaded.registry.component_types)

        plugin_config = raw_config.get(loaded.name, {})
        if plugin_config is None:
            plugin_config = {}
        if not isinstance(plugin_config, Mapping):
            raise ConfigurationError(f"Configuration section {loaded.name!r} must be a mapping")

        for compiler in loaded.registry.plan_compilers:
            try:
                compiler(plugin_config, builder)
            except AutoSpanError:
                raise
            except (KeyError, TypeError, ValueError) as exc:
                detail = str(exc).strip("'") or type(exc).__name__
                raise ConfigurationError(
                    f"Invalid {loaded.name!r} configuration: {detail}"
                ) from exc
        scenarios.extend(loaded.registry.scenario_contributions)
        builder.add_metadata(
            f"plugin.{loaded.name}",
            {
                "version": loaded.plugin.descriptor.version,
                "distribution": loaded.distribution,
            },
        )

    components = tuple(builder.components)
    for component in components:
        if component.type_id not in known_component_types:
            raise PlanError(
                f"Component {component.name!r} uses unregistered type {component.type_id!r}"
            )

    ordered = topological_order(components)
    validate_resource_claims(ordered)
    return RuntimePlan(
        components=ordered,
        scenarios=tuple(scenarios),
        metadata=builder.metadata,
    )
