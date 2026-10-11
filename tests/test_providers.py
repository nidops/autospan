"""Provider mechanism: providers are separate from project plugins."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from autospan.api import (
    AUTOSPAN_PLUGIN_API_VERSION,
    Contract,
    LazyProvider,
    PlanError,
    PluginDescriptor,
    ProviderContribution,
    RuntimePlan,
    provider_component,
)
from autospan._core.discovery import load_selected_plugins
from autospan._core.evidence_store import LocalEvidenceStore
from autospan._core.planner import compile_runtime_plan
from autospan._core.providers import (
    ProviderComponentFactory,
    collect_providers,
    validate_provider_components,
)
from autospan._core.runtime import RuntimeSession
from autospan.capabilities.flasher import FLASHER, FlashRequest, FlashResult, Flasher, FlashStatus
from autospan.capabilities.flasher.fake import FakeFlasher, FakeFlasherFactory
from autospan.testing.conformance import check_flasher


class _Project:
    """One project plugin that selects a provider but never registers it."""

    def __init__(self, provider: str, **options: Any) -> None:
        self.descriptor = PluginDescriptor("project", "1", AUTOSPAN_PLUGIN_API_VERSION)
        self._provider = provider
        self._options = options

    def register(self, registrar: Any) -> None:
        registrar.plan_compiler(
            lambda config, plan: plan.add_component(
                provider_component("target", FLASHER, self._provider, self._options)
            )
        )


def _plan(project: _Project):
    plugins = load_selected_plugins(["project"], source=lambda name: (project, "project-dist"))
    return compile_runtime_plan(plugins=plugins, raw_config={}, config_dir=Path.cwd())


class _ScriptFlasherFactory:
    @contextmanager
    def open(
        self, context: Any, dependencies: Mapping[str, object], config: Mapping[str, Any]
    ) -> Iterator[Any]:
        class Script:
            def flash(self, request: FlashRequest) -> FlashResult:
                return FlashResult(
                    FlashStatus.OK, f"{config['tool']}:{request.parameters['alias']}"
                )

        yield Script()


FAKE = ProviderContribution(FLASHER, "fake-flasher", FakeFlasherFactory())
SCRIPT = ProviderContribution(FLASHER, "script-flasher", _ScriptFlasherFactory())


def _open(plan, providers, tmp_path: Path):
    table = collect_providers(providers)
    validate_provider_components(plan.components, table)
    return RuntimeSession(
        plan=plan,
        factories={"autospan.provider": ProviderComponentFactory(table)},
        evidence=LocalEvidenceStore(tmp_path / "evidence"),
    )


def test_project_swaps_provider_by_name_only(tmp_path: Path) -> None:
    for select, options in (("fake-flasher", {}), ("script-flasher", {"tool": "gdb-script"})):
        plan = _plan(_Project(select, **options))
        with _open(plan, (FAKE, SCRIPT), tmp_path) as runtime:
            flasher = runtime.require("target", Flasher)
            assert flasher.flash(FlashRequest(parameters={"alias": "a"})).ok


def test_provider_is_not_a_project_plugin_contribution() -> None:
    project = _Project("fake-flasher")
    loaded = load_selected_plugins(["project"], source=lambda name: (project, "project-dist"))
    assert not hasattr(loaded[0].registry, "providers")


def test_unknown_provider_fails_during_plan_validation() -> None:
    plan = _plan(_Project("missing-tool"))
    table = collect_providers((FAKE,))
    with pytest.raises(PlanError, match=r"no provider 'missing-tool'.*available: fake-flasher"):
        validate_provider_components(plan.components, table)


def test_provider_for_wrong_contract_is_rejected() -> None:
    debugger = Contract("debugger", 1)
    wrong = ProviderContribution(debugger, "fake-flasher", FakeFlasherFactory())
    plan = _plan(_Project("fake-flasher"))
    table = collect_providers((wrong,))
    with pytest.raises(PlanError, match="implements contract 'debugger', not 'flasher'"):
        validate_provider_components(plan.components, table)


def test_contract_version_mismatch_is_rejected() -> None:
    v2 = Contract("flasher", 2)
    with pytest.raises(PlanError, match="provided as both v1 and v2"):
        collect_providers((FAKE, ProviderContribution(v2, "other-flasher", FakeFlasherFactory())))


def test_duplicate_provider_is_rejected() -> None:
    with pytest.raises(PlanError, match="defined more than once"):
        collect_providers((FAKE, FAKE))


def test_provider_must_satisfy_its_contract(tmp_path: Path) -> None:
    class Wrong:
        @contextmanager
        def open(self, context, dependencies, config):
            yield object()

    bad = ProviderContribution(FLASHER, "bad-flasher", Wrong())
    plan = _plan(_Project("bad-flasher"))
    with pytest.raises(Exception, match="does not satisfy contract 'flasher'"):
        with _open(plan, (bad,), tmp_path):
            pass


def test_conformance_helper_accepts_kernel_fake(tmp_path: Path) -> None:
    result = check_flasher(FakeFlasher(), tmp_path / "image.bin")
    assert result.ok


def _lazy_session(tmp_path: Path, events: list[str]) -> RuntimeSession:
    class Tracked:
        @contextmanager
        def open(self, context, dependencies, config):
            events.append("open")
            try:
                yield FakeFlasher()
            finally:
                events.append("close")

    tracked = ProviderContribution(FLASHER, "tracked-flasher", Tracked())
    spec = provider_component("target", FLASHER, "tracked-flasher", lazy=True)
    table = collect_providers((tracked,))
    return RuntimeSession(
        plan=RuntimePlan(components=(spec,)),
        factories={"autospan.provider": ProviderComponentFactory(table)},
        evidence=LocalEvidenceStore(tmp_path / "e"),
    )


def test_lazy_provider_opens_on_first_use_and_closes_at_teardown(tmp_path: Path) -> None:
    events: list[str] = []
    with _lazy_session(tmp_path, events) as runtime:
        lazy = runtime.require("target", LazyProvider)
        assert events == [] and not lazy.opened
        assert lazy.resolve() is lazy.resolve()
        assert events == ["open"]
    assert events == ["open", "close"]


def test_unused_lazy_provider_never_opens(tmp_path: Path) -> None:
    events: list[str] = []
    with _lazy_session(tmp_path, events):
        pass
    assert events == []


def test_kernel_debugger_fake_conforms() -> None:
    from autospan.capabilities.debugger.fake import FakeDebugger
    from autospan.testing.conformance import check_debugger

    check_debugger(FakeDebugger())
