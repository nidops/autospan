"""Lazy discovery and loading of project plugins."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from importlib import metadata

from autospan.api.errors import PluginError
from autospan.api.plugin import (
    AUTOSPAN_PLUGIN_API_VERSION,
    PLUGIN_ENTRY_POINT_GROUP,
    SUPPORTED_PLUGIN_API_VERSIONS,
    AutoSpanPlugin,
)
from autospan._core.registry import ContributionRegistry


@dataclass(frozen=True, slots=True)
class DiscoveredPlugin:
    name: str
    value: str
    distribution: str | None


@dataclass(frozen=True, slots=True)
class LoadedPlugin:
    name: str
    plugin: AutoSpanPlugin
    registry: ContributionRegistry
    distribution: str | None


def _dist_name(entry: metadata.EntryPoint) -> str | None:
    dist = getattr(entry, "dist", None)
    name = getattr(dist, "name", None)
    return None if name is None else str(name)


def _entries_by_name() -> dict[str, list[metadata.EntryPoint]]:
    by_name: dict[str, list[metadata.EntryPoint]] = {}
    seen: set[tuple[str, str, str | None]] = set()
    for entry in metadata.entry_points().select(group=PLUGIN_ENTRY_POINT_GROUP):
        key = (entry.name, entry.value, _dist_name(entry))
        if key in seen:
            continue
        seen.add(key)
        by_name.setdefault(entry.name, []).append(entry)
    return by_name


def _pick(name: str, matches: list[metadata.EntryPoint]) -> metadata.EntryPoint:
    if len(matches) > 1:
        dists = ", ".join(sorted(str(_dist_name(ep) or "unknown") for ep in matches))
        raise PluginError(f"AutoSpan plugin {name!r} is ambiguous; provided by: {dists}")
    return matches[0]


def discover_plugins() -> tuple[DiscoveredPlugin, ...]:
    """List installed project plugins without importing them."""

    result: list[DiscoveredPlugin] = []
    for name, matches in _entries_by_name().items():
        try:
            entry = _pick(name, matches)
        except PluginError:
            for candidate in matches:
                result.append(DiscoveredPlugin(name, candidate.value, _dist_name(candidate)))
            continue
        result.append(DiscoveredPlugin(name, entry.value, _dist_name(entry)))
    return tuple(sorted(result, key=lambda item: (item.name, item.distribution or "")))


def _validate_and_register(
    *, requested_name: str, plugin: AutoSpanPlugin, distribution: str | None
) -> LoadedPlugin:
    descriptor = getattr(plugin, "descriptor", None)
    if descriptor is None:
        raise PluginError(f"Plugin {requested_name!r} has no PluginDescriptor")
    if descriptor.name != requested_name:
        raise PluginError(
            f"Plugin {requested_name!r} loaded descriptor {descriptor.name!r}; names must match"
        )
    if descriptor.api_version not in SUPPORTED_PLUGIN_API_VERSIONS:
        supported = ", ".join(str(item) for item in sorted(SUPPORTED_PLUGIN_API_VERSIONS))
        raise PluginError(
            f"Plugin {requested_name!r} targets AutoSpan plugin API {descriptor.api_version}, "
            f"host {AUTOSPAN_PLUGIN_API_VERSION} supports: {supported}. "
            "Install compatible host/plugin versions or migrate the plugin to a supported API. "
            "Changing the descriptor alone does not establish compatibility. See docs/PLUGIN_AUTHORING.md."
        )
    registry = ContributionRegistry()
    plugin.register(registry)
    return LoadedPlugin(requested_name, plugin, registry, distribution)


PluginSource = Callable[[str], tuple[AutoSpanPlugin, str | None]]


def _entry_point_source(by_name: Mapping[str, list[metadata.EntryPoint]]) -> PluginSource:
    def load(name: str) -> tuple[AutoSpanPlugin, str | None]:
        matches = by_name.get(name, [])
        if not matches:
            rendered = ", ".join(sorted(by_name)) or "<none>"
            raise PluginError(f"AutoSpan plugin {name!r} is not installed; available: {rendered}")
        entry = _pick(name, matches)
        try:
            candidate = entry.load()
        except Exception as exc:
            raise PluginError(
                f"AutoSpan plugin {name!r} from {_dist_name(entry) or 'unknown'} failed to import: {exc}"
            ) from exc
        plugin = candidate() if isinstance(candidate, type) else candidate
        return plugin, _dist_name(entry)

    return load


def load_selected_plugins(
    names: Iterable[str], *, source: PluginSource | None = None
) -> tuple[LoadedPlugin, ...]:
    """Load exactly the selected project plugins, once each, in selection order."""

    requested = tuple(dict.fromkeys(name.strip() for name in names if name.strip()))
    load = source or _entry_point_source(_entries_by_name())
    result: list[LoadedPlugin] = []
    for name in requested:
        plugin, distribution = load(name)
        result.append(
            _validate_and_register(requested_name=name, plugin=plugin, distribution=distribution)
        )
    return tuple(result)
