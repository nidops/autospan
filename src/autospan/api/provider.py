"""Swappable implementations of narrow kernel-defined tool contracts.

Providers are *not* project plugins. Provider packages publish
:class:`ProviderContribution` objects through the ``autospan.providers`` entry-point
group. A project plugin selects a provider by name in configuration and plans a
provider-backed component with :func:`provider_component`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from autospan.api.component import ComponentFactory, ComponentSpec, ResourceClaim

PROVIDER_TYPE_ID = "autospan.provider"
PROVIDER_ENTRY_POINT_GROUP = "autospan.providers"

T = TypeVar("T")
_NAME = re.compile(r"^[a-z][a-z0-9_.-]*$")


@dataclass(frozen=True, slots=True)
class Contract:
    """A named, versioned kernel contract for a swappable external tool.

    ``protocol`` should be a ``runtime_checkable`` ``typing.Protocol``; when
    given, every provider instance is checked against it at acquisition time.
    """

    name: str
    version: int = 1
    protocol: type | None = field(default=None, compare=False, hash=False)

    def __post_init__(self) -> None:
        if not _NAME.fullmatch(self.name):
            raise ValueError(f"Contract name {self.name!r} must be a lowercase dotted identifier")
        if self.version < 1:
            raise ValueError("Contract.version must be >= 1")

    def conforms(self, instance: object) -> bool:
        """Check runtime protocol membership; this does not test driver behavior."""
        return self.protocol is None or isinstance(instance, self.protocol)


@dataclass(frozen=True, slots=True)
class ProviderContribution:
    """One named implementation of a kernel contract."""

    contract: Contract
    name: str
    factory: ComponentFactory
    description: str = ""

    def __post_init__(self) -> None:
        if not _NAME.fullmatch(self.name):
            raise ValueError(f"Provider name {self.name!r} must be a lowercase dotted identifier")


class LazyProvider(Generic[T]):
    """Provider opened on first resolve and closed by the active runtime.

    Resolve/use only within the owning runtime scope. Callers must serialize
    resolution; this facade does not coordinate concurrent first acquisitions.
    """

    def __init__(self, opener: Callable[[], T]) -> None:
        self._opener = opener
        self._instance: T | None = None
        self._opened = False

    @property
    def opened(self) -> bool:
        return self._opened

    def resolve(self) -> T:
        """Return the cached instance, propagating acquisition or contract failures."""
        if not self._opened:
            self._instance = self._opener()
            self._opened = True
        return self._instance  # type: ignore[return-value]


def provider_component(
    name: str,
    contract: Contract,
    provider: str,
    options: Mapping[str, Any] | None = None,
    *,
    dependencies: tuple[str, ...] = (),
    claims: tuple[ResourceClaim, ...] = (),
    gates: tuple[str, ...] = (),
    lazy: bool = False,
) -> ComponentSpec:
    """Plan a component backed by one installed provider.

    Host planning fails before hardware opens when the named provider is missing,
    implements another contract, or targets another contract version.
    ``options`` reaches its factory as immutable config. ``lazy=True`` defers
    acquisition until resolve, while the runtime still owns cleanup.
    """

    return ComponentSpec(
        name=name,
        type_id=PROVIDER_TYPE_ID,
        dependencies=dependencies,
        claims=claims,
        gates=gates,
        config={
            "contract": contract.name,
            "contract_version": contract.version,
            "provider": provider,
            "options": dict(options or {}),
            "lazy": lazy,
        },
    )
