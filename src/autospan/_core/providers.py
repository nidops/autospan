"""Provider resolution and the generic component factory that opens providers."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from typing import Any

from autospan.api.component import ComponentContext, ComponentSpec
from autospan.api.errors import LifecycleError, PlanError
from autospan.api.provider import PROVIDER_TYPE_ID, LazyProvider, ProviderContribution
from autospan._core.provider_discovery import load_provider

ProviderTable = Mapping[tuple[str, str], ProviderContribution]


def referenced_provider_names(components: Iterable[ComponentSpec]) -> tuple[str, ...]:
    """Return provider names referenced by provider-backed components, once each."""

    return tuple(
        dict.fromkeys(
            str(spec.config["provider"]) for spec in components if spec.type_id == PROVIDER_TYPE_ID
        )
    )


def collect_providers(
    contributions: Iterable[ProviderContribution],
) -> dict[tuple[str, str], ProviderContribution]:
    """Validate provider contributions and index them by ``(contract, provider)``."""

    table: dict[tuple[str, str], ProviderContribution] = {}
    versions: dict[str, int] = {}
    for item in contributions:
        key = (item.contract.name, item.name)
        if key in table:
            raise PlanError(
                f"Provider {item.name!r} of contract {item.contract.name!r} is defined more than once"
            )
        known_version = versions.get(item.contract.name)
        if known_version is not None and known_version != item.contract.version:
            raise PlanError(
                f"Contract {item.contract.name!r} is provided as both v{known_version} "
                f"and v{item.contract.version}"
            )
        versions[item.contract.name] = item.contract.version
        table[key] = item
    return table


def load_referenced_providers(
    components: Iterable[ComponentSpec],
) -> dict[tuple[str, str], ProviderContribution]:
    """Load only providers actually named by the compiled plan."""

    return collect_providers(load_provider(name) for name in referenced_provider_names(components))


def validate_provider_components(
    components: tuple[ComponentSpec, ...], table: ProviderTable
) -> None:
    """Fail planning when a component names a missing, wrong-contract or wrong-version provider."""

    for spec in components:
        if spec.type_id != PROVIDER_TYPE_ID:
            continue
        contract = str(spec.config["contract"])
        provider = str(spec.config["provider"])
        found = table.get((contract, provider))
        if found is None:
            same_name = [item for (_, name), item in table.items() if name == provider]
            if same_name:
                actual = same_name[0].contract.name
                raise PlanError(
                    f"Component {spec.name!r}: provider {provider!r} implements contract {actual!r}, "
                    f"not {contract!r}"
                )
            available = ", ".join(sorted(p for c, p in table if c == contract)) or "<none>"
            raise PlanError(
                f"Component {spec.name!r}: no provider {provider!r} for contract {contract!r}; "
                f"available: {available}"
            )
        if found.contract.version != spec.config["contract_version"]:
            raise PlanError(
                f"Component {spec.name!r} needs contract {contract!r} "
                f"v{spec.config['contract_version']}, provider {provider!r} implements "
                f"v{found.contract.version}"
            )


class ProviderComponentFactory:
    """Open the provider named by a component's config and check its contract."""

    def __init__(self, table: ProviderTable) -> None:
        self._table = table

    @contextmanager
    def open(
        self,
        context: ComponentContext,
        dependencies: Mapping[str, object],
        config: Mapping[str, Any],
    ) -> Iterator[object]:
        contribution = self._table[(str(config["contract"]), str(config["provider"]))]

        def check(instance: object) -> object:
            if not contribution.contract.conforms(instance):
                raise LifecycleError(
                    f"Provider {contribution.name!r} yielded {type(instance).__name__}, which does "
                    f"not satisfy contract {contribution.contract.name!r}"
                )
            return instance

        manager = contribution.factory.open
        with ExitStack() as stack:
            cleanup_failed: BaseException | None = None

            def acquire() -> object:
                nonlocal cleanup_failed
                if cleanup_failed is not None:
                    raise LifecycleError(
                        "Lazy provider cleanup failed; cannot retry"
                    ) from cleanup_failed
                attempt = ExitStack()
                try:
                    instance = attempt.enter_context(
                        manager(context, dependencies, config["options"])
                    )
                    check(instance)
                except BaseException as exc:
                    try:
                        # A provider cannot suppress a host contract failure.
                        attempt.__exit__(type(exc), exc, exc.__traceback__)
                    except BaseException as cleanup_error:
                        cleanup_failed = cleanup_error
                        exc.add_note(f"Provider acquisition cleanup failed: {cleanup_error!r}")
                    raise
                stack.enter_context(attempt.pop_all())
                return instance

            if config.get("lazy"):
                lazy = LazyProvider(acquire)
                try:
                    yield lazy
                finally:
                    # Retirement synchronizes with resolve before the stack closes.
                    lazy._retire()
            else:
                yield acquire()
