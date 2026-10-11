from dataclasses import dataclass
from pathlib import Path

import pytest

from autospan.api import (
    AUTOSPAN_PLUGIN_API_VERSION,
    ComponentSpec,
    PlanError,
    PluginDescriptor,
    ScenarioContribution,
)
from autospan._core.discovery import LoadedPlugin
from autospan._core.planner import compile_runtime_plan
from autospan._core.registry import ContributionRegistry


@dataclass
class Plugin:
    descriptor: PluginDescriptor


def _loaded(name, registry):
    return LoadedPlugin(
        name=name,
        plugin=Plugin(
            PluginDescriptor(name=name, version="1", api_version=AUTOSPAN_PLUGIN_API_VERSION)
        ),
        registry=registry,
        distribution=None,
    )


def test_plugin_config_compiles_to_runtime_plan_without_opening_hardware():
    registry = ContributionRegistry()
    registry.component_type("demo.device", object())
    opened = False

    def compile_config(config, plan):
        nonlocal opened
        assert config["answer"] == 42
        assert opened is False
        plan.add_component(ComponentSpec("device", "demo.device"))

    registry.plan_compiler(compile_config)
    registry.scenarios(ScenarioContribution("demo"))
    plan = compile_runtime_plan(
        plugins=(_loaded("demo", registry),),
        raw_config={"demo": {"answer": 42}},
        config_dir=Path.cwd(),
    )
    assert [component.name for component in plan.components] == ["device"]
    assert plan.scenarios == (ScenarioContribution("demo"),)


def test_unregistered_component_type_is_rejected():
    registry = ContributionRegistry()
    registry.plan_compiler(lambda config, plan: plan.add_component(ComponentSpec("x", "missing")))
    with pytest.raises(PlanError, match="unregistered type"):
        compile_runtime_plan(
            plugins=(_loaded("demo", registry),), raw_config={}, config_dir=Path.cwd()
        )
