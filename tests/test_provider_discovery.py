from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager

import pytest

from autospan.api import Contract, PluginError, ProviderContribution
from autospan._core import provider_discovery


class _Factory:
    @contextmanager
    def open(self, context, dependencies: Mapping[str, object], config) -> Iterator[object]:
        yield object()


class _Entries(list):
    def select(self, *, group: str):
        assert group == "autospan.providers"
        return self


class _EntryPoint:
    def __init__(
        self, name: str, value: str, contribution: object, dist: str = "provider-dist"
    ) -> None:
        self.name = name
        self.value = value
        self._contribution = contribution
        self.dist = type("Dist", (), {"name": dist})()
        self.loads = 0

    def load(self):
        self.loads += 1
        return self._contribution


def _provider(name: str = "tool") -> ProviderContribution:
    return ProviderContribution(Contract("flasher"), name, _Factory())


def test_provider_discovery_does_not_import_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _EntryPoint("tool", "pkg:PROVIDER", _provider())
    monkeypatch.setattr(provider_discovery.metadata, "entry_points", lambda: _Entries([entry]))
    found = provider_discovery.discover_providers()
    assert [(item.name, item.distribution) for item in found] == [("tool", "provider-dist")]
    assert entry.loads == 0


def test_load_provider_imports_only_named_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    wanted = _EntryPoint("wanted", "a:PROVIDER", _provider("wanted"))
    other = _EntryPoint("other", "b:PROVIDER", _provider("other"))
    monkeypatch.setattr(
        provider_discovery.metadata, "entry_points", lambda: _Entries([wanted, other])
    )
    loaded = provider_discovery.load_provider("wanted")
    assert loaded.name == "wanted"
    assert wanted.loads == 1
    assert other.loads == 0


def test_duplicate_provider_name_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    a = _EntryPoint("tool", "a:PROVIDER", _provider(), "dist-a")
    b = _EntryPoint("tool", "b:PROVIDER", _provider(), "dist-b")
    monkeypatch.setattr(provider_discovery.metadata, "entry_points", lambda: _Entries([a, b]))
    with pytest.raises(PluginError, match="provider 'tool' is ambiguous"):
        provider_discovery.load_provider("tool")


def test_provider_entry_point_name_must_match_contribution(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _EntryPoint("tool", "pkg:PROVIDER", _provider("different"))
    monkeypatch.setattr(provider_discovery.metadata, "entry_points", lambda: _Entries([entry]))
    with pytest.raises(PluginError, match="loaded contribution 'different'"):
        provider_discovery.load_provider("tool")
