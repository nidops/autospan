from __future__ import annotations

from dataclasses import dataclass

import pytest

from autospan.api import AUTOSPAN_PLUGIN_API_VERSION, AutoSpanPlugin, PluginDescriptor, PluginError
from autospan._core import discovery


class _Entries(list):
    def select(self, *, group: str):
        del group
        return self


class _EntryPoint:
    def __init__(self, name: str, value: str, plugin: object, dist: str = "demo-dist") -> None:
        self.name = name
        self.value = value
        self._plugin = plugin
        self.dist = type("Dist", (), {"name": dist})()
        self.loads = 0

    def load(self):
        self.loads += 1
        return self._plugin


@dataclass(frozen=True)
class _Plugin(AutoSpanPlugin):
    descriptor: PluginDescriptor

    def register(self, registrar) -> None:
        del registrar


def test_discovery_does_not_import_external_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = _Plugin(PluginDescriptor("external-demo", "1", AUTOSPAN_PLUGIN_API_VERSION))
    entry = _EntryPoint("external-demo", "pkg:PLUGIN", plugin)
    monkeypatch.setattr(discovery.metadata, "entry_points", lambda: _Entries([entry]))

    found = discovery.discover_plugins()

    assert any(item.name == "external-demo" for item in found)
    assert entry.loads == 0


def test_selected_external_plugin_is_loaded_once(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = _Plugin(PluginDescriptor("external-demo", "1", AUTOSPAN_PLUGIN_API_VERSION))
    entry = _EntryPoint("external-demo", "pkg:PLUGIN", plugin)
    monkeypatch.setattr(discovery.metadata, "entry_points", lambda: _Entries([entry]))

    loaded = discovery.load_selected_plugins(["external-demo", "external-demo"])

    assert [item.name for item in loaded] == ["external-demo"]
    assert entry.loads == 1


def test_incompatible_plugin_api_is_rejected_before_planning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _Plugin(PluginDescriptor("external-demo", "1", AUTOSPAN_PLUGIN_API_VERSION + 1))
    entry = _EntryPoint("external-demo", "pkg:PLUGIN", plugin)
    monkeypatch.setattr(discovery.metadata, "entry_points", lambda: _Entries([entry]))

    with pytest.raises(PluginError, match="targets AutoSpan plugin API"):
        discovery.load_selected_plugins(["external-demo"])


@pytest.mark.parametrize("unsupported", [0, 2, 3])
def test_only_initial_plugin_api_is_supported(
    monkeypatch: pytest.MonkeyPatch,
    unsupported: int,
) -> None:
    assert AUTOSPAN_PLUGIN_API_VERSION == 1
    plugin = _Plugin(PluginDescriptor("unsupported-demo", "0.1.0", unsupported))
    entry = _EntryPoint("unsupported-demo", "pkg:PLUGIN", plugin)
    monkeypatch.setattr(discovery.metadata, "entry_points", lambda: _Entries([entry]))

    with pytest.raises(PluginError, match=f"targets AutoSpan plugin API {unsupported}"):
        discovery.load_selected_plugins(["unsupported-demo"])


def test_duplicate_external_plugin_name_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = _Plugin(PluginDescriptor("external-demo", "1", AUTOSPAN_PLUGIN_API_VERSION))
    entries = _Entries(
        [
            _EntryPoint("external-demo", "a:PLUGIN", plugin, "dist-a"),
            _EntryPoint("external-demo", "b:PLUGIN", plugin, "dist-b"),
        ]
    )
    monkeypatch.setattr(discovery.metadata, "entry_points", lambda: entries)

    with pytest.raises(PluginError, match="ambiguous"):
        discovery.load_selected_plugins(["external-demo"])
