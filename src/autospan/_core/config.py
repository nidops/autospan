"""Minimal host configuration loader plus explicit command-line overrides."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from autospan.api.errors import ConfigurationError


class _StrictLoader(yaml.SafeLoader):
    """Reject duplicate explicit keys before applying YAML merge precedence."""

    def __init__(self, stream: Any) -> None:
        super().__init__(stream)
        self._checked: set[int] = set()
        self._flattening: set[int] = set()

    def flatten_mapping(self, node: Any) -> None:
        identity = id(node)
        if identity in self._flattening:
            raise yaml.constructor.ConstructorError(
                None, None, "recursive YAML merge", node.start_mark
            )
        if identity not in self._checked:
            seen: set[Any] = set()
            merge_key = object()
            for key_node, _ in node.value:
                key = (
                    merge_key
                    if key_node.tag == "tag:yaml.org,2002:merge"
                    else key_node.value
                    if key_node.tag == "tag:yaml.org,2002:value"
                    else self.construct_object(key_node)
                )
                try:
                    duplicate = key in seen
                    seen.add(key)
                except TypeError as exc:
                    raise yaml.constructor.ConstructorError(
                        None, None, "unhashable mapping key", key_node.start_mark
                    ) from exc
                if duplicate:
                    raise yaml.constructor.ConstructorError(
                        "while constructing a mapping",
                        node.start_mark,
                        f"duplicate explicit key {'<<' if key is merge_key else key!r}",
                        key_node.start_mark,
                    )
            self._checked.add(identity)
        self._flattening.add(identity)
        try:
            super().flatten_mapping(node)
        finally:
            self._flattening.remove(identity)


def load_config(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.load(path.read_text(encoding="utf-8"), Loader=_StrictLoader)
    except OSError as exc:
        raise ConfigurationError(f"Could not read AutoSpan config {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Invalid YAML in AutoSpan config {path}: {exc}") from exc

    if payload is None:
        payload = {}
    if not isinstance(payload, Mapping):
        raise ConfigurationError("AutoSpan root configuration must be a mapping")
    return dict(payload)


def apply_overrides(config: Mapping[str, Any], overrides: Iterable[str]) -> dict[str, Any]:
    """Apply ``dotted.key=value`` overrides; values are parsed as YAML scalars/flows.

    Example: ``--set my_project.bench_config=C:/benches/b12.yaml`` or
    ``--set autospan.plugins=[my-project]``. Overrides are applied before any
    project plugin is loaded, so they may also change project selection.
    """

    result = deepcopy(dict(config))
    for raw in overrides:
        key, sep, value_text = raw.partition("=")
        key = key.strip()
        if not sep or not key or any(not part.strip() for part in key.split(".")):
            raise ConfigurationError(f"Override {raw!r} must look like dotted.key=value")
        try:
            value = yaml.load(value_text, Loader=_StrictLoader) if value_text.strip() else ""
        except yaml.YAMLError as exc:
            raise ConfigurationError(f"Override {raw!r} has an invalid YAML value: {exc}") from exc
        node: dict[str, Any] = result
        parts = [part.strip() for part in key.split(".")]
        for part in parts[:-1]:
            child = node.get(part)
            if child is None:
                child = {}
                node[part] = child
            elif not isinstance(child, dict):
                raise ConfigurationError(
                    f"Override {raw!r} cannot descend into non-mapping key {part!r}"
                )
            node = child
        node[parts[-1]] = value
    return result


def selected_plugins(config: Mapping[str, Any]) -> tuple[str, ...]:
    host = config.get("autospan", {})
    if host is None:
        host = {}
    if not isinstance(host, Mapping):
        raise ConfigurationError("autospan configuration section must be a mapping")
    unknown = sorted(set(host) - {"plugins", "pytest"})
    if unknown:
        raise ConfigurationError(f"autospan has unknown key(s): {', '.join(unknown)}")
    plugins = host.get("plugins", ())
    if isinstance(plugins, str):
        plugins = [plugins]
    if not isinstance(plugins, (list, tuple)) or not all(isinstance(item, str) for item in plugins):
        raise ConfigurationError("autospan.plugins must be a list of project plugin names")
    return tuple(item.strip() for item in plugins if item.strip())
