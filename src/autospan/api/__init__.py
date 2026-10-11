"""Stable public AutoSpan plugin/runtime API."""

from autospan.api.commands import (
    CommandCancelled,
    CommandContext,
    CommandContribution,
    CommandOutcome,
    CommandShell,
    Completion,
)
from autospan.api.component import (
    ClaimMode,
    ComponentContext,
    ComponentFactory,
    ComponentSpec,
    ResourceClaim,
    ResourceYield,
    RuntimePlan,
    ScenarioContribution,
)
from autospan.api.evidence import (
    Artifact,
    ArtifactReservation,
    Evidence,
    EvidenceContext,
    StepVerdict,
)
from autospan.api.errors import (
    AutoSpanError,
    ConfigurationError,
    GateError,
    LifecycleError,
    PlanError,
    PluginError,
)
from autospan.api.frontend import FrontendContext, FrontendContribution, FrontendHandler
from autospan.api.gates import GateContribution
from autospan.api.periodic import PeriodicScheduler, PeriodicState, PeriodicStats, PeriodicTask
from autospan.api.plugin import (
    AUTOSPAN_PLUGIN_API_VERSION,
    PLUGIN_ENTRY_POINT_GROUP,
    SUPPORTED_PLUGIN_API_VERSIONS,
    AutoSpanPlugin,
    PlanBuilder,
    PlanCompiler,
    PluginDescriptor,
    PluginRegistrar,
)
from autospan.api.provider import (
    PROVIDER_ENTRY_POINT_GROUP,
    Contract,
    LazyProvider,
    ProviderContribution,
    provider_component,
)
from autospan.api.runtime import Runtime

__all__ = [
    "AUTOSPAN_PLUGIN_API_VERSION",
    "PLUGIN_ENTRY_POINT_GROUP",
    "SUPPORTED_PLUGIN_API_VERSIONS",
    "Artifact",
    "ArtifactReservation",
    "AutoSpanError",
    "AutoSpanPlugin",
    "ClaimMode",
    "CommandContribution",
    "CommandContext",
    "CommandCancelled",
    "Completion",
    "CommandOutcome",
    "CommandShell",
    "ComponentContext",
    "ComponentFactory",
    "ComponentSpec",
    "Contract",
    "ConfigurationError",
    "Evidence",
    "EvidenceContext",
    "StepVerdict",
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
    "PROVIDER_ENTRY_POINT_GROUP",
    "PluginDescriptor",
    "PluginError",
    "PluginRegistrar",
    "ProviderContribution",
    "ResourceClaim",
    "ResourceYield",
    "Runtime",
    "RuntimePlan",
    "ScenarioContribution",
    "provider_component",
]
