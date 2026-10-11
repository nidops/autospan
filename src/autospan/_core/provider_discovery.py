"""Lazy discovery and loading of provider packages."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata

from autospan.api.errors import PluginError
from autospan.api.provider import PROVIDER_ENTRY_POINT_GROUP, ProviderContribution


@dataclass(frozen=True, slots=True)
class DiscoveredProvider:
    name: str
    value: str
    distribution: str | None


def _dist_name(entry: metadata.EntryPoint) -> str | None:
    dist = getattr(entry, "dist", None)
    name = getattr(dist, "name", None)
    return None if name is None else str(name)


def _entries_by_name() -> dict[str, list[metadata.EntryPoint]]:
    by_name: dict[str, list[metadata.EntryPoint]] = {}
    seen: set[tuple[str, str, str | None]] = set()
    for entry in metadata.entry_points().select(group=PROVIDER_ENTRY_POINT_GROUP):
        key = (entry.name, entry.value, _dist_name(entry))
        if key in seen:
            continue
        seen.add(key)
        by_name.setdefault(entry.name, []).append(entry)
    return by_name


def discover_providers() -> tuple[DiscoveredProvider, ...]:
    """List installed provider entry points without importing implementations."""

    result: list[DiscoveredProvider] = []
    for name, matches in _entries_by_name().items():
        for entry in matches:
            result.append(DiscoveredProvider(name, entry.value, _dist_name(entry)))
    return tuple(sorted(result, key=lambda item: (item.name, item.distribution or "")))


def load_provider(name: str) -> ProviderContribution:
    """Load one provider by globally unique provider name."""

    matches = _entries_by_name().get(name, [])
    if not matches:
        rendered = ", ".join(sorted(_entries_by_name())) or "<none>"
        raise PluginError(f"AutoSpan provider {name!r} is not installed; available: {rendered}")
    if len(matches) > 1:
        dists = ", ".join(sorted(str(_dist_name(ep) or "unknown") for ep in matches))
        raise PluginError(f"AutoSpan provider {name!r} is ambiguous; provided by: {dists}")
    entry = matches[0]
    try:
        candidate = entry.load()
    except Exception as exc:
        raise PluginError(
            f"AutoSpan provider {name!r} from {_dist_name(entry) or 'unknown'} failed to import: {exc}"
        ) from exc
    contribution = candidate() if isinstance(candidate, type) else candidate
    if not isinstance(contribution, ProviderContribution):
        raise PluginError(f"AutoSpan provider {name!r} did not expose ProviderContribution")
    if contribution.name != name:
        raise PluginError(
            f"Provider entry point {name!r} loaded contribution {contribution.name!r}; names must match"
        )
    return contribution
