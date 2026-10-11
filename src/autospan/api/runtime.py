"""Public live-runtime view."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager
from typing import TypeVar

from autospan.api.evidence import Evidence
from autospan.api.periodic import PeriodicScheduler

T = TypeVar("T")


class Runtime:
    """Read-only lookup facade over an active component graph.

    The owning frontend controls acquisition and teardown. Components, providers
    and this view may only be used while that owning scope is active.
    """

    def __init__(
        self,
        instances: Mapping[str, object],
        evidence: Evidence,
        handover: Callable[[str], AbstractContextManager[None]] | None = None,
        *,
        periodic: PeriodicScheduler | None = None,
        check_usable: Callable[[], None] | None = None,
    ) -> None:
        self._instances = instances
        self._evidence = evidence
        self._periodic = periodic
        self._handover = handover
        self._check_usable = check_usable

    def _ensure_usable(self) -> None:
        if self._check_usable is not None:
            self._check_usable()

    @property
    def evidence(self) -> Evidence:
        """Evidence interface correlated with the active AutoSpan runtime/test context."""
        return self._evidence

    @property
    def periodic(self) -> PeriodicScheduler:
        """Runtime-owned scheduler shared by components and frontends."""
        self._ensure_usable()
        if self._periodic is None:
            raise LookupError("This Runtime view is not attached to a periodic scheduler")
        return self._periodic

    def get(self, name: str) -> object:
        """Return a named component or raise an informative ``KeyError``."""
        self._ensure_usable()
        try:
            return self._instances[name]
        except KeyError as exc:
            available = ", ".join(sorted(self._instances)) or "<none>"
            raise KeyError(f"Unknown runtime component {name!r}; available: {available}") from exc

    def require(self, name: str, expected_type: type[T]) -> T:
        """Return a named component after validating its runtime type."""
        value = self.get(name)
        if not isinstance(value, expected_type):
            raise TypeError(
                f"Runtime component {name!r} is {type(value).__name__}, "
                f"expected {expected_type.__name__}"
            )
        return value

    def handover(self, resource_key: str) -> AbstractContextManager[None]:
        """Release holders while an external tool uses the resource inside the block.

        Holders must implement ``ResourceYield``. Reacquisition runs in dependency
        order even after a block failure; failures raise ``LifecycleError`` or
        are attached as notes to the original exception. Nested handovers of the
        same key are rejected. Stop conflicting work before entering the block.
        Other live runtimes claiming the key must close first; new claims are
        blocked until reacquisition finishes. Reservations are process-local.
        """
        self._ensure_usable()
        if self._handover is None:
            raise LookupError("This runtime does not support resource handover")
        return self._handover(resource_key)

    def names(self) -> tuple[str, ...]:
        """Return all live component names in deterministic order."""
        self._ensure_usable()
        return tuple(sorted(self._instances))

    def items(self) -> Iterator[tuple[str, object]]:
        """Iterate over live component name/object pairs."""
        self._ensure_usable()
        return iter(tuple(self._instances.items()))
