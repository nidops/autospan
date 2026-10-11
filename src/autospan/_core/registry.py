"""Internal project-plugin contribution registry."""

from __future__ import annotations

from dataclasses import dataclass, field

from autospan.api.commands import CommandContribution
from autospan.api.component import ComponentFactory, ScenarioContribution
from autospan.api.errors import PluginError
from autospan.api.frontend import FrontendContribution
from autospan.api.gates import GateContribution
from autospan.api.plugin import PlanCompiler, PluginRegistrar


@dataclass(slots=True)
class ContributionRegistry(PluginRegistrar):
    """Collect declarations from one project plugin."""

    component_types: dict[str, ComponentFactory] = field(default_factory=dict)
    plan_compilers: list[PlanCompiler] = field(default_factory=list)
    scenario_contributions: list[ScenarioContribution] = field(default_factory=list)
    commands: list[CommandContribution] = field(default_factory=list)
    gates: list[GateContribution] = field(default_factory=list)
    frontends: list[FrontendContribution] = field(default_factory=list)

    def component_type(self, type_id: str, factory: ComponentFactory) -> None:
        if not type_id.strip():
            raise PluginError("Component type_id must not be empty")
        if type_id in self.component_types:
            raise PluginError(f"Duplicate component type registration: {type_id!r}")
        self.component_types[type_id] = factory

    def plan_compiler(self, compiler: PlanCompiler) -> None:
        self.plan_compilers.append(compiler)

    def scenarios(self, contribution: ScenarioContribution) -> None:
        self.scenario_contributions.append(contribution)

    def command(self, contribution: CommandContribution) -> None:
        if any(existing.path == contribution.path for existing in self.commands):
            rendered = " ".join(contribution.path)
            raise PluginError(f"Duplicate console command registration: {rendered}")
        self.commands.append(contribution)

    def gate(self, contribution: GateContribution) -> None:
        if any(existing.name == contribution.name for existing in self.gates):
            raise PluginError(f"Duplicate gate registration: {contribution.name!r}")
        self.gates.append(contribution)

    def frontend(self, contribution: FrontendContribution) -> None:
        if any(existing.name == contribution.name for existing in self.frontends):
            raise PluginError(f"Duplicate frontend registration: {contribution.name!r}")
        self.frontends.append(contribution)
