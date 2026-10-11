"""Canonical ``autospan test`` orchestration path."""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Iterable

import pytest

from autospan.api.component import ScenarioContribution, normalize_pytest_ini
from autospan.api.errors import ConfigurationError, PluginError
from autospan._core.evidence_store import LocalEvidenceStore
from autospan._core.host import HostBuild
from autospan.frontends.pytest_plugin import AutoSpanPytestBridge

# Hosted runs never inherit an arbitrary pytest.ini/pyproject found next to an
# installed scenario package or in the operator's working directory.
_HOSTED_ADDOPTS = "-ra --strict-markers"


def _operator_pytest_section(config: Mapping[str, Any]) -> Mapping[str, Any]:
    host = config.get("autospan") or {}
    section = host.get("pytest") if isinstance(host, Mapping) else None
    if section is None:
        return {}
    if not isinstance(section, Mapping):
        raise ConfigurationError("autospan.pytest must be a mapping")
    unknown = sorted(set(section) - {"ini", "plugins"})
    if unknown:
        raise ConfigurationError(f"autospan.pytest has unknown key(s): {', '.join(unknown)}")
    return section


def hosted_pytest_plugins(config: Mapping[str, Any]) -> tuple[str, ...]:
    """Extra pytest plugins to load with ``-p`` (autoload is disabled in hosted runs)."""

    plugins = _operator_pytest_section(config).get("plugins", ())
    if isinstance(plugins, str):
        plugins = [plugins]
    if not isinstance(plugins, (list, tuple)) or not all(isinstance(p, str) for p in plugins):
        raise ConfigurationError("autospan.pytest.plugins must be a list of module names")
    return tuple(p.strip() for p in plugins if p.strip())


def hosted_ini(
    scenarios: Iterable[ScenarioContribution],
    config: Mapping[str, Any],
    *,
    cache_dir: str,
) -> str:
    """Render the hosted pytest.ini from plugin defaults plus operator overrides."""

    raw_operator = _operator_pytest_section(config).get("ini") or {}
    if not isinstance(raw_operator, Mapping):
        raise ConfigurationError("autospan.pytest.ini must be a mapping")
    try:
        operator = normalize_pytest_ini(raw_operator, owner="autospan.pytest.ini")
    except ValueError as exc:
        raise ConfigurationError(str(exc)) from exc
    addopts = [_HOSTED_ADDOPTS]
    values: dict[str, tuple[str, str]] = {}
    for contribution in scenarios:
        owner = f"scenarios {contribution.package!r}"
        for key, value in contribution.pytest_ini.items():
            if key == "addopts":
                addopts.append(value)
            elif key not in operator and key in values and values[key][0] != value:
                raise PluginError(
                    f"pytest ini {key!r} conflicts: {values[key][1]} sets {values[key][0]!r}, "
                    f"{owner} sets {value!r}; set autospan.pytest.ini.{key} to decide"
                )
            else:
                values[key] = (value, owner)
    for key, value in operator.items():
        if key == "addopts":
            addopts.append(value)
        else:
            values[key] = (value, "operator")
    lines = ["[pytest]", "addopts = " + " ".join(item for item in addopts if item.strip())]
    lines.extend(f"{key} = {value}" for key, (value, _) in values.items())
    lines.append(f"cache_dir = {cache_dir}")
    return "\n".join(lines) + "\n"


def resolve_scenario_paths(
    contributions: Iterable[ScenarioContribution],
) -> tuple[Path, ...]:
    """Resolve installed scenario-package contributions to concrete directories."""

    result: list[Path] = []
    for contribution in contributions:
        spec = importlib.util.find_spec(contribution.package)
        if spec is None or not spec.submodule_search_locations:
            raise PluginError(f"Scenario package {contribution.package!r} is not importable")
        locations = list(dict.fromkeys(spec.submodule_search_locations))
        if len(locations) != 1:
            raise PluginError(
                f"Scenario package {contribution.package!r} has ambiguous locations: {locations}"
            )
        path = Path(locations[0]) / contribution.relative_path
        if not path.is_dir():
            raise PluginError(f"Scenario directory does not exist: {path}")
        result.append(path.resolve())
    return tuple(dict.fromkeys(result))


def _rootdir(paths: tuple[Path, ...]) -> Path:
    common = Path(os.path.commonpath([str(path) for path in paths]))
    # Keep the scenario package name in node ids: <package>/<file>::<test>.
    return common.parent if len(paths) == 1 else common


def split_selection(
    pytest_args: Iterable[str], scenario_paths: tuple[Path, ...], rootdir: Path
) -> tuple[list[str], list[str]]:
    """Separate scenario file/node-id selections from the other pytest arguments.

    ``autospan test -- scenarios/node/test_x.py::test_y`` narrows the run the
    same way plain pytest would. Paths may be given relative to the working
    directory or to the hosted rootdir (as printed in node ids); only paths
    inside a contributed scenario directory are treated as selections.
    """

    selections: list[str] = []
    rest: list[str] = []
    for token in pytest_args:
        if token.startswith("-"):
            rest.append(token)
            continue
        base, sep, node = token.partition("::")
        for candidate in (Path(base), rootdir / base):
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved.exists() and any(
                resolved == root or root in resolved.parents for root in scenario_paths
            ):
                selections.append(str(resolved) + sep + node)
                break
        else:
            rest.append(token)
    return selections, rest


def run_tests(
    *,
    host: HostBuild,
    evidence_root: Path,
    pytest_args: tuple[str, ...] = (),
) -> int:
    """Run only scenarios explicitly contributed by the selected AutoSpan plugins."""

    scenario_paths = resolve_scenario_paths(host.plan.scenarios)
    if not scenario_paths:
        selected = ", ".join(loaded.name for loaded in host.plugins) or "<none>"
        raise PluginError(
            "No scenarios were contributed by the selected plugins "
            f"({selected}). AutoSpan refuses implicit pytest discovery."
        )

    evidence = LocalEvidenceStore(evidence_root)
    bridge = AutoSpanPytestBridge(host, evidence)
    exit_code = 1
    error: BaseException | None = None
    try:
        ini = evidence.root / "pytest.ini"
        # The cache lives beside the evidence runs so `--lf`/`--ff` work across runs.
        cache_dir = (Path(evidence_root) / ".pytest_cache").resolve().as_posix()
        ini.write_text(
            hosted_ini(host.plan.scenarios, host.config, cache_dir=cache_dir), encoding="utf-8"
        )
        args = [
            "-c",
            str(ini),
            "--rootdir",
            str(_rootdir(scenario_paths)),
        ]
        for plugin in hosted_pytest_plugins(host.config):
            args.extend(["-p", plugin])
        selections, rest = split_selection(pytest_args, scenario_paths, _rootdir(scenario_paths))
        args.extend(selections or [str(path) for path in scenario_paths])
        args.extend(rest)
        evidence.event("pytest_invoked", args=args)
        previous_autoload = os.environ.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD")
        os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        try:
            exit_code = int(pytest.main(args, plugins=[bridge]))
        finally:
            if previous_autoload is None:
                os.environ.pop("PYTEST_DISABLE_PLUGIN_AUTOLOAD", None)
            else:
                os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = previous_autoload
        return exit_code
    except BaseException as exc:
        error = exc
        exit_code = max(exit_code, 1)
        raise
    finally:
        try:
            # Also covers pytest aborting before its sessionfinish hook can run.
            bridge.close_session_runtime(exit_code)
        except BaseException as cleanup_error:
            exit_code = max(exit_code, 1)
            if error is None:
                error = cleanup_error
                raise
            error.add_note(f"Session runtime cleanup failed: {cleanup_error!r}")
        finally:
            try:
                evidence.close(
                    outcome="passed" if exit_code == 0 else "failed",
                    metadata={
                        "plugins": [loaded.name for loaded in host.plugins],
                        "components": [spec.name for spec in host.plan.components],
                        "enabled_gates": sorted(bridge.enabled_gates),
                        "pytest_exit_code": exit_code,
                    },
                )
            except BaseException as reporting_error:
                if error is None:
                    raise
                error.add_note(f"Evidence finalization failed: {reporting_error!r}")
