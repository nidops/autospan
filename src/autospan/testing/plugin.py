"""Public helpers to unit-test a plugin without importing AutoSpan internals."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autospan.api.component import ComponentSpec
from autospan.api.errors import PlanError
from autospan.api.plugin import AutoSpanPlugin
from autospan._core.registry import ContributionRegistry


@dataclass(slots=True)
class RecordingPlanBuilder:
    """In-memory :class:`~autospan.api.PlanBuilder` that records declarations."""

    config_dir: Path
    components: list[ComponentSpec] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)

    def add_component(self, spec: ComponentSpec) -> None:
        """Record a component declaration without acquiring it."""
        self.components.append(spec)

    def add_metadata(self, key: str, value: object) -> None:
        """Record a unique metadata key or raise ``PlanError``."""
        if key in self.metadata:
            raise PlanError(f"Duplicate RuntimePlan metadata key: {key!r}")
        self.metadata[key] = value

    def component(self, name: str) -> ComponentSpec:
        """Return a recorded component by name or raise ``KeyError``."""
        for spec in self.components:
            if spec.name == name:
                return spec
        raise KeyError(name)


def collect_contributions(plugin: AutoSpanPlugin) -> ContributionRegistry:
    """Run ``plugin.register`` against a fresh registry and return what it declared.

    The returned object exposes ``component_types``, ``plan_compilers``,
    ``scenario_contributions``, ``commands``, ``gates`` and ``frontends``.
    """

    registry = ContributionRegistry()
    plugin.register(registry)
    return registry


def compile_plugin(
    plugin: AutoSpanPlugin,
    config: Mapping[str, Any] | None = None,
    *,
    config_dir: Path,
) -> RecordingPlanBuilder:
    """Record compiler output for a plugin's config section; errors propagate.

    This helper does not perform host graph, gate or provider validation.
    """

    builder = RecordingPlanBuilder(config_dir=Path(config_dir))
    for compiler in collect_contributions(plugin).plan_compilers:
        compiler(dict(config or {}), builder)
    return builder
