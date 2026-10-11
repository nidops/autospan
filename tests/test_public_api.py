import re
from pathlib import Path

import autospan.api as api


def test_public_api_is_intentionally_small():
    expected = {
        "AUTOSPAN_PLUGIN_API_VERSION",
        "PLUGIN_ENTRY_POINT_GROUP",
        "PROVIDER_ENTRY_POINT_GROUP",
        "SUPPORTED_PLUGIN_API_VERSIONS",
        "Artifact",
        "ArtifactReservation",
        "AutoSpanError",
        "AutoSpanPlugin",
        "ClaimMode",
        "ComponentContext",
        "CommandContribution",
        "CommandContext",
        "CommandCancelled",
        "Completion",
        "CommandOutcome",
        "CommandShell",
        "ComponentFactory",
        "ComponentSpec",
        "Contract",
        "ConfigurationError",
        "Evidence",
        "EvidenceContext",
        "FrontendContext",
        "FrontendContribution",
        "FrontendHandler",
        "GateContribution",
        "GateError",
        "LazyProvider",
        "LifecycleError",
        "PlanBuilder",
        "PlanCompiler",
        "PlanError",
        "PeriodicScheduler",
        "PeriodicState",
        "PeriodicStats",
        "PeriodicTask",
        "PluginDescriptor",
        "PluginError",
        "PluginRegistrar",
        "ProviderContribution",
        "ResourceClaim",
        "ResourceYield",
        "Runtime",
        "RuntimePlan",
        "ScenarioContribution",
        "StepVerdict",
        "provider_component",
    }
    assert set(api.__all__) == expected


def test_initial_plugin_api_matches_authoritative_documentation():
    assert api.AUTOSPAN_PLUGIN_API_VERSION == 1
    assert api.SUPPORTED_PLUGIN_API_VERSIONS == frozenset({1})
    root = Path(__file__).resolve().parents[1]
    statements = {
        "README.md": r"Plugins declare API version `(\d+)`",
        "docs/CONSOLE.md": r"Plugin API \*\*(\d+)\*\* uses",
        "docs/PLUGIN_AUTHORING.md": r"The initial public plugin contract is API (\d+)",
        "RELEASING.md": r"with plugin API (\d+)\.",
    }
    for path, pattern in statements.items():
        versions = re.findall(pattern, (root / path).read_text(encoding="utf-8"))
        assert versions == [str(api.AUTOSPAN_PLUGIN_API_VERSION)], path
