"""Extension-point contracts: project selection, gates, outcome, frontends, overrides."""

from __future__ import annotations

import json
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from contextlib import contextmanager
from typing import Any, Iterator

import pytest

from autospan.api import (
    AUTOSPAN_PLUGIN_API_VERSION,
    AutoSpanPlugin,
    ComponentSpec,
    ConfigurationError,
    FrontendContribution,
    GateContribution,
    GateError,
    PlanError,
    PluginDescriptor,
    PluginError,
    PluginRegistrar,
    RuntimePlan,
    ScenarioContribution,
)
from autospan._core.config import apply_overrides
from autospan._core.discovery import load_selected_plugins
from autospan._core.evidence_store import LocalEvidenceStore
from autospan._core.host import HostBuild, _collect_gates, build_host
from autospan._core.runtime import RuntimeSession
from autospan.frontends.cli import main as cli_main
from autospan.frontends.frontend_host import HostFrontendContext
from autospan.frontends.test_command import run_tests


@dataclass(frozen=True)
class _Plugin(AutoSpanPlugin):
    descriptor: PluginDescriptor
    gates: tuple[GateContribution, ...] = ()
    frontends: tuple[FrontendContribution, ...] = ()
    compiled: list[str] = field(default_factory=list, compare=False, hash=False)

    def register(self, registrar: PluginRegistrar) -> None:
        for gate in self.gates:
            registrar.gate(gate)
        for frontend in self.frontends:
            registrar.frontend(frontend)

        def compiler(config: Any, builder: Any) -> None:
            self.compiled.append(self.descriptor.name)

        registrar.plan_compiler(compiler)


def _source(*plugins: _Plugin):
    by_name = {plugin.descriptor.name: plugin for plugin in plugins}

    def load(name: str):
        if name not in by_name:
            raise PluginError(f"AutoSpan plugin {name!r} is not installed")
        return by_name[name], "test-dist"

    return load


def _plugin(name: str, *, api_version: int = AUTOSPAN_PLUGIN_API_VERSION, **kw: Any) -> _Plugin:
    return _Plugin(PluginDescriptor(name, "1.0", api_version=api_version), **kw)


# --------------------------------------------------------------------------- project selection


def test_only_explicitly_selected_projects_are_loaded() -> None:
    project = _plugin("project")
    unrelated = _plugin("unrelated")
    loaded = load_selected_plugins(["project"], source=_source(project, unrelated))
    assert [item.name for item in loaded] == ["project"]


def test_duplicate_project_selection_loads_once() -> None:
    project = _plugin("project")
    loaded = load_selected_plugins(["project", "project"], source=_source(project))
    assert [item.name for item in loaded] == ["project"]


def test_missing_selected_project_is_rejected() -> None:
    project = _plugin("project")
    with pytest.raises(PluginError, match="plugin 'ghost' is not installed"):
        load_selected_plugins(["ghost"], source=_source(project))


def test_unknown_plugin_api_versions_fail() -> None:
    current = _plugin("current")
    assert load_selected_plugins(["current"], source=_source(current))[0].name == "current"
    future = _plugin("future", api_version=99)
    with pytest.raises(PluginError, match="plugin API 99"):
        load_selected_plugins(["future"], source=_source(future))


# --------------------------------------------------------------------------- gates


def _loaded(*plugins: _Plugin):
    return load_selected_plugins([p.descriptor.name for p in plugins], source=_source(*plugins))


def test_plugin_gates_extend_the_defaults() -> None:
    flash = _plugin(
        "flash", gates=(GateContribution("flashing", "reflash", implies=("destructive",)),)
    )
    gates = {gate.name: gate for gate in _collect_gates(_loaded(flash))}
    assert set(gates) == {"hardware", "destructive", "flashing"}
    host = HostBuild(
        plugins=(),
        plan=RuntimePlan(components=()),
        factories={},
        commands=(),
        gates=tuple(gates.values()),
    )
    assert host.gate_closure("flashing") == ("flashing", "destructive", "hardware")


@pytest.mark.parametrize(
    ("gate", "message"),
    [
        (GateContribution("hardware"), "already defined by 'autospan'"),
        (GateContribution("x", implies=("nope",)), "implies unknown gate 'nope'"),
    ],
)
def test_invalid_gates_are_rejected(gate: GateContribution, message: str) -> None:
    with pytest.raises(PluginError, match=message):
        _collect_gates(_loaded(_plugin("p", gates=(gate,))))


def test_gate_cycles_are_rejected() -> None:
    plugin = _plugin(
        "p",
        gates=(GateContribution("a", implies=("b",)), GateContribution("b", implies=("a",))),
    )
    with pytest.raises(PluginError, match="Gate implication cycle"):
        _collect_gates(_loaded(plugin))


def test_gate_names_are_validated() -> None:
    with pytest.raises(ValueError):
        GateContribution("Bad-Name")
    with pytest.raises(ValueError):
        ComponentSpec("x", "t", gates=("Nope!",))


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    @contextmanager
    def open(self, context: Any, dependencies: Any, config: Any) -> Iterator[Any]:
        name = config.get("name", "?")
        assert context.outcome is None
        self.events.append(("open", name))
        yield name
        self.events.append(("close", context.outcome))


def _gated_plan() -> RuntimePlan:
    return RuntimePlan(
        components=(
            ComponentSpec("sim", "rec", config={"name": "sim"}),
            ComponentSpec("bench", "rec", config={"name": "bench"}, gates=("destructive",)),
        )
    )


def test_gated_component_fails_before_anything_opens(tmp_path: Path) -> None:
    recorder = _Recorder()
    session = RuntimeSession(
        plan=_gated_plan(),
        factories={"rec": recorder},
        evidence=LocalEvidenceStore(tmp_path),
        enabled_gates=frozenset({"hardware"}),
    )
    with pytest.raises(GateError) as info:
        session.__enter__()
    assert info.value.missing == ("destructive",)
    assert recorder.events == []


def test_outcome_reaches_factory_close(tmp_path: Path) -> None:
    recorder = _Recorder()
    session = RuntimeSession(
        plan=_gated_plan(),
        factories={"rec": recorder},
        evidence=LocalEvidenceStore(tmp_path),
        enabled_gates=None,
    )
    with session as runtime:
        assert runtime.get("bench") == "bench"
        session.set_outcome("failed")
    assert recorder.events[-2:] == [("close", "failed"), ("close", "failed")]
    with pytest.raises(ValueError):
        session.set_outcome("weird")


def test_unknown_component_gate_is_a_plan_error(monkeypatch: pytest.MonkeyPatch) -> None:
    @dataclass(frozen=True)
    class Gated(AutoSpanPlugin):
        descriptor: PluginDescriptor = PluginDescriptor(
            "gated", "1", api_version=AUTOSPAN_PLUGIN_API_VERSION
        )

        def register(self, registrar: PluginRegistrar) -> None:
            registrar.component_type("rec", _Recorder())
            registrar.plan_compiler(
                lambda config, builder: builder.add_component(
                    ComponentSpec("x", "rec", gates=("flashing",))
                )
            )

    import autospan._core.host as host_module

    monkeypatch.setattr(
        host_module,
        "load_selected_plugins",
        lambda names: load_selected_plugins(names, source=_source(Gated())),  # type: ignore[arg-type]
    )
    with pytest.raises(PlanError, match="unknown gate"):
        build_host(plugin_names=("gated",), config={})


# --------------------------------------------------------------------------- pytest bridge


def _scenario_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gates: tuple[GateContribution, ...] = ()
) -> tuple[HostBuild, _Recorder]:
    # Unique package per test: pytest runs in-process and caches imported modules.
    package_name = "gate_scenarios_" + tmp_path.name.lower().replace("-", "_")
    package = tmp_path / "pkgroot" / package_name
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / f"test_gated_{package_name}.py").write_text(
        textwrap.dedent(
            """
            import pytest

            def test_plain(runtime):
                assert runtime.get("sim") == "sim"

            @pytest.mark.destructive
            def test_destructive():
                pass

            @pytest.mark.flashing
            def test_flash():
                pass

            def test_uses_bench(runtime):
                runtime.get("bench")

            def test_failure(runtime):
                assert False
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(package.parent))
    recorder = _Recorder()
    from autospan._core.host import DEFAULT_GATES

    host = HostBuild(
        plugins=(),
        plan=RuntimePlan(
            components=(
                ComponentSpec("sim", "rec", config={"name": "sim"}),
                ComponentSpec("bench", "rec", config={"name": "bench"}, gates=("hardware",)),
            ),
            scenarios=(ScenarioContribution(package_name, relative_path="."),),
        ),
        factories={"rec": recorder},
        commands=(),
        gates=DEFAULT_GATES + gates,
    )
    return host, recorder


def _outcomes(evidence_root: Path) -> dict[str, str]:
    (run,) = [
        path for path in evidence_root.iterdir() if path.is_dir() and not path.name.startswith(".")
    ]
    result: dict[str, str] = {}
    for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record["name"] == "pytest_phase_finished":
            event = record["attributes"]
            name = event["nodeid"].rsplit("::", 1)[-1]
            if event["phase"] == "call" or event["outcome"] != "passed":
                result.setdefault(name, event["outcome"])
    return result


def test_gates_drive_skips_and_component_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, recorder = _scenario_host(
        tmp_path, monkeypatch, (GateContribution("flashing", implies=("hardware",)),)
    )
    code = run_tests(host=host, evidence_root=tmp_path / "ev", pytest_args=("-q",))
    assert code == 0
    # 'bench' is hardware-gated, so every runtime user skips instead of touching it.
    assert set(_outcomes(tmp_path / "ev").values()) == {"skipped"}
    assert len(_outcomes(tmp_path / "ev")) == 5
    assert recorder.events == []


def test_enabled_gates_run_everything_and_report_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, recorder = _scenario_host(
        tmp_path, monkeypatch, (GateContribution("flashing", implies=("hardware",)),)
    )
    code = run_tests(
        host=host,
        evidence_root=tmp_path / "ev",
        pytest_args=("-q", "--hardware", "--destructive", "--flashing"),
    )
    assert code == 1
    outcomes = _outcomes(tmp_path / "ev")
    assert outcomes["test_plain"] == "passed"
    assert outcomes["test_destructive"] == "passed"
    assert outcomes["test_flash"] == "passed"
    assert outcomes["test_failure"] == "failed"
    closes = [value for kind, value in recorder.events if kind == "close"]
    assert "failed" in closes and "passed" in closes


def test_destructive_without_hardware_names_hardware(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    host, _ = _scenario_host(tmp_path, monkeypatch)
    run_tests(
        host=host,
        evidence_root=tmp_path / "ev",
        pytest_args=("-rs", "--destructive", "-k", "destructive"),
    )
    assert "requires --hardware" in capsys.readouterr().out


@pytest.mark.parametrize("fixture", ["runtime", "session_runtime"])
@pytest.mark.parametrize(
    "gate,flags,allowed",
    [
        ("destructive", ("--destructive",), False),
        ("destructive", ("--destructive", "--hardware"), True),
        ("flashing", ("--flashing", "--destructive"), False),
        ("flashing", ("--flashing", "--hardware"), False),
        ("flashing", ("--flashing", "--destructive", "--hardware"), True),
    ],
)
def test_unmarked_runtime_requires_all_implied_component_gates(
    tmp_path, monkeypatch, fixture, gate, flags, allowed
):
    from autospan._core.host import DEFAULT_GATES

    package_name = "component_gates_" + tmp_path.name.lower()
    package = tmp_path / package_name
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "test_device.py").write_text(
        f"def test_device({fixture}):\n    assert {fixture}.get('bench') == 'bench'\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    recorder = _Recorder()
    host = HostBuild(
        (),
        RuntimePlan(
            (ComponentSpec("bench", "rec", config={"name": "bench"}, gates=(gate,)),),
            (ScenarioContribution(package_name, "."),),
        ),
        {"rec": recorder},
        (),
        gates=DEFAULT_GATES + (GateContribution("flashing", implies=("destructive",)),),
    )
    assert run_tests(host=host, evidence_root=tmp_path / "ev", pytest_args=("-q", *flags)) == 0
    assert _outcomes(tmp_path / "ev") == {"test_device": "passed" if allowed else "skipped"}
    assert recorder.events == ([("open", "bench"), ("close", "passed")] if allowed else [])


def test_hosted_run_writes_deterministic_ini(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, _ = _scenario_host(tmp_path, monkeypatch)
    run_tests(host=host, evidence_root=tmp_path / "ev", pytest_args=("--collect-only", "-q"))
    (run,) = [p for p in (tmp_path / "ev").iterdir() if p.is_dir() and not p.name.startswith(".")]
    ini = (run / "pytest.ini").read_text(encoding="utf-8")
    assert "--strict-markers" in ini and "cache_dir" in ini


def test_node_id_selection_narrows_the_hosted_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autospan.frontends.test_command import resolve_selections

    host, _ = _scenario_host(tmp_path, monkeypatch)
    (contribution,) = host.plan.scenarios
    root = tmp_path / "pkgroot" / contribution.package
    test_file = next(root.glob("test_*.py"))
    selections = resolve_selections(
        (f"{contribution.package}/{test_file.name}::test_plain",), (root.resolve(),), root.parent
    )
    assert selections == [f"{test_file.resolve()}::test_plain"]
    with pytest.raises(PluginError, match="outside contributed"):
        resolve_selections(("missing.py",), (root.resolve(),), root.parent)

    code = run_tests(
        host=host,
        evidence_root=tmp_path / "ev",
        pytest_args=(str(test_file) + "::test_destructive", "--hardware", "--destructive"),
    )
    assert code == 0
    assert _outcomes(tmp_path / "ev") == {"test_destructive": "passed"}


def test_hosted_ini_merges_plugin_defaults_and_operator_overrides() -> None:
    from autospan.frontends.test_command import hosted_ini, hosted_pytest_plugins

    first = ScenarioContribution("a", pytest_ini={"log_cli": True, "addopts": ["-p", "no:x"]})
    second = ScenarioContribution("b", pytest_ini={"log_cli": "true", "log_cli_level": "INFO"})
    config = {
        "autospan": {
            "pytest": {
                "ini": {"log_cli_level": "DEBUG", "addopts": "--tb=short"},
                "plugins": ["pytest_html.plugin"],
            }
        }
    }
    ini = hosted_ini((first, second), config, cache_dir="C:/cache")
    lines = ini.splitlines()
    assert lines[0] == "[pytest]"
    assert lines[1] == "addopts = -ra --strict-markers -p no:x --tb=short"
    assert "log_cli = true" in lines and "log_cli_level = DEBUG" in lines
    assert lines[-1] == "cache_dir = C:/cache"
    assert hosted_pytest_plugins(config) == ("pytest_html.plugin",)


def test_hosted_ini_rejects_conflicts_and_reserved_keys() -> None:
    from autospan.frontends.test_command import hosted_ini

    with pytest.raises(PluginError, match="log_cli_level"):
        hosted_ini(
            (
                ScenarioContribution("a", pytest_ini={"log_cli_level": "INFO"}),
                ScenarioContribution("b", pytest_ini={"log_cli_level": "DEBUG"}),
            ),
            {},
            cache_dir="c",
        )
    with pytest.raises(ValueError, match="owned by AutoSpan"):
        ScenarioContribution("a", pytest_ini={"cache_dir": "x"})
    with pytest.raises(ConfigurationError, match="unknown key"):
        hosted_ini((), {"autospan": {"pytest": {"inis": {}}}}, cache_dir="c")
    with pytest.raises(ConfigurationError, match="owned by AutoSpan"):
        hosted_ini((), {"autospan": {"pytest": {"ini": {"testpaths": "x"}}}}, cache_dir="c")


def test_operator_override_resolves_conflicting_plugin_ini_defaults():
    from autospan.frontends.test_command import hosted_ini

    scenarios = tuple(
        ScenarioContribution(name, pytest_ini={"log_cli_level": level, "addopts": f"-m {name}"})
        for name, level in (("a", "INFO"), ("b", "WARNING"), ("c", "ERROR"))
    )
    ini = hosted_ini(
        scenarios,
        {"autospan": {"pytest": {"ini": {"log_cli_level": "DEBUG", "addopts": "--tb=short"}}}},
        cache_dir="c",
    )
    assert "log_cli_level = DEBUG\n" in ini
    assert "addopts = -ra --strict-markers -m a -m b -m c --tb=short\n" in ini


def test_plugin_ini_defaults_reach_the_hosted_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, _ = _scenario_host(tmp_path, monkeypatch)
    (contribution,) = host.plan.scenarios
    scenarios = (ScenarioContribution(contribution.package, ".", pytest_ini={"log_cli": True}),)
    plan = RuntimePlan(components=host.plan.components, scenarios=scenarios)
    hosted = HostBuild(
        plugins=(), plan=plan, factories=host.factories, commands=(), gates=host.gates
    )
    run_tests(host=hosted, evidence_root=tmp_path / "ev", pytest_args=("-k", "plain"))
    (run,) = [p for p in (tmp_path / "ev").iterdir() if p.is_dir() and not p.name.startswith(".")]
    assert "log_cli = true" in (run / "pytest.ini").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- frontends & CLI


def test_frontend_context_opens_the_same_runtime(tmp_path: Path) -> None:
    recorder = _Recorder()
    host = HostBuild(
        plugins=(),
        plan=RuntimePlan(components=(ComponentSpec("sim", "rec", config={"name": "sim"}),)),
        factories={"rec": recorder},
        commands=(),
        config={"x": 1},
    )
    seen: list[Any] = []

    def handler(ctx: Any, args: tuple[str, ...]) -> int:
        seen.append((ctx.config["x"], sorted(ctx.enabled_gates), args))
        with ctx.open_runtime() as runtime:
            seen.append(runtime.get("sim"))
        return 3

    host = HostBuild(
        **{
            **{f: getattr(host, f) for f in host.__dataclass_fields__},
            "frontends": (FrontendContribution("demo.tool", handler),),
        }
    )
    context = HostFrontendContext(host, evidence_root=tmp_path, frontend_name="demo.tool")
    assert host.frontend("demo.tool").handler(context, ("--x",)) == 3
    assert seen == [(1, [], ("--x",)), "sim"]
    assert recorder.events[-1] == ("close", "passed")
    with pytest.raises(PluginError, match="Unknown frontend"):
        host.frontend("nope")


def test_frontend_context_requires_explicit_hardware_gate(tmp_path: Path) -> None:
    recorder = _Recorder()
    host = HostBuild(
        plugins=(),
        plan=RuntimePlan(
            components=(ComponentSpec("hw", "rec", config={"name": "hw"}, gates=("hardware",)),)
        ),
        factories={"rec": recorder},
        commands=(),
    )

    safe = HostFrontendContext(host, evidence_root=tmp_path / "safe", frontend_name="demo.safe")
    with pytest.raises(GateError, match="require --hardware"):
        with safe.open_runtime():
            pass
    assert recorder.events == []

    enabled = HostFrontendContext(
        host,
        evidence_root=tmp_path / "enabled",
        enabled_gates=frozenset({"hardware"}),
        frontend_name="demo.enabled",
    )
    with enabled.open_runtime() as runtime:
        assert runtime.get("hw") == "hw"
    assert recorder.events[-1] == ("close", "passed")


@pytest.mark.parametrize(
    "original", [None, ValueError("operation"), KeyboardInterrupt("operation")]
)
def test_frontend_finalization_retains_original_failure(tmp_path, monkeypatch, original):
    outcomes = []

    class Factory:
        @contextmanager
        def open(self, context, dependencies, config):
            try:
                yield object()
            finally:
                outcomes.append(context.outcome)

    host = HostBuild((), RuntimePlan((ComponentSpec("sim", "rec"),)), {"rec": Factory()}, ())
    context = HostFrontendContext(host, evidence_root=tmp_path)
    close = LocalEvidenceStore.close
    reporting = OSError("manifest failed")

    def fail_close(self, **kwargs):
        close(self, **kwargs)
        raise reporting

    monkeypatch.setattr(LocalEvidenceStore, "close", fail_close)
    expected = original if original is not None else reporting
    with pytest.raises(type(expected)) as failure:
        with context.open_runtime():
            if original is not None:
                raise original
    assert failure.value is expected
    assert outcomes == ["failed" if original else "passed"]
    if original is not None:
        assert repr(reporting) in " ".join(original.__notes__)


def test_frontend_names_are_validated() -> None:
    with pytest.raises(ValueError):
        FrontendContribution("Bad Name", lambda ctx, args: 0)


def test_unknown_host_config_key_is_rejected() -> None:
    from autospan._core.config import selected_plugins

    with pytest.raises(ConfigurationError, match="autospan has unknown key"):
        selected_plugins({"autospan": {"plugins": [], "typo": True}})


def test_overrides_parse_yaml_and_create_paths() -> None:
    base = {"autospan": {"plugins": ["project"]}, "proj": {"bench_config": "a.yaml"}}
    result = apply_overrides(
        base, ["proj.bench_config=b.yaml", "autospan.plugins=[project, other]", "new.n=3"]
    )
    assert result["proj"]["bench_config"] == "b.yaml"
    assert result["autospan"]["plugins"] == ["project", "other"]
    assert result["new"] == {"n": 3}
    assert base["proj"]["bench_config"] == "a.yaml"
    with pytest.raises(ConfigurationError):
        apply_overrides(base, ["missing-equals"])
    with pytest.raises(ConfigurationError):
        apply_overrides(base, ["proj.bench_config.deeper=1"])


def test_cli_plan_honours_set_and_reports_gates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "autospan.yaml"
    config.write_text("autospan:\n  plugins: []\n", encoding="utf-8")
    assert cli_main(["plan", "--config", str(config)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["plugins"] == []
    assert {g["name"] for g in payload["gates"]} == {"hardware", "destructive"}


def test_cli_run_unknown_frontend_is_a_clean_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "autospan.yaml"
    config.write_text("autospan:\n  plugins: []\n", encoding="utf-8")
    assert cli_main(["run", "nope.tool", "--config", str(config), "--", "--flag"]) == 2
    output = capsys.readouterr()
    assert "Unknown frontend 'nope.tool'" in output.err
    assert output.out == ""


# --------------------------------------------------------------------------- testing helpers


def test_python_dash_m_autospan_is_available() -> None:
    import importlib.util

    assert importlib.util.find_spec("autospan.__main__") is not None
    assert sys.version_info >= (3, 11)
