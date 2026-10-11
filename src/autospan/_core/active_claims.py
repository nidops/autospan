"""Atomic in-process reservations across concurrently live runtime sessions.

Plans validate claims internally; this registry prevents two separately opened
sessions from simultaneously acquiring an exclusive physical/logical resource.
It is not an inter-process lock and cannot normalize hardware aliases.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from threading import RLock

from autospan.api.component import ClaimMode, ComponentSpec
from autospan.api.errors import LifecycleError

_LOCK = RLock()
# key -> (owner token, component name, mode). Token identity, not equality, owns a lease.
_ACTIVE: dict[str, list[tuple[object, str, ClaimMode]]] = {}
_HANDOVERS: dict[str, object] = {}


def acquire_runtime_claims(owner: object, components: tuple[ComponentSpec, ...]) -> None:
    """Reserve a complete plan atomically or fail before any component is opened."""
    requested = [(spec.name, claim.key, claim.mode) for spec in components for claim in spec.claims]
    with _LOCK:
        for name, key, mode in requested:
            if key in _HANDOVERS:
                raise LifecycleError(
                    f"Runtime resource {key!r} is handed over to an external tool; "
                    f"cannot open {name!r}"
                )
            for _, holder, held_mode in _ACTIVE.get(key, ()):
                if mode is ClaimMode.EXCLUSIVE or held_mode is ClaimMode.EXCLUSIVE:
                    raise LifecycleError(
                        f"Runtime resource {key!r} already claimed by live component "
                        f"{holder!r}; cannot open {name!r}"
                    )
        try:
            for name, key, mode in requested:
                _ACTIVE.setdefault(key, []).append((owner, name, mode))
        except BaseException:
            release_runtime_claims(owner)
            raise


@contextmanager
def reserve_runtime_handover(owner: object, key: str) -> Iterator[None]:
    """Exclude other sessions until this session has reacquired a yielded resource."""
    reservation = object()
    try:
        with _LOCK:
            if key in _HANDOVERS:
                raise LifecycleError(f"Resource {key!r} is already handed over")
            if any(holder is not owner for holder, _, _ in _ACTIVE.get(key, ())):
                raise LifecycleError(f"Resource {key!r} is claimed by another live runtime")
            _HANDOVERS[key] = reservation
        yield
    finally:
        with _LOCK:
            if _HANDOVERS.get(key) is reservation:
                del _HANDOVERS[key]


def release_runtime_claims(owner: object) -> None:
    """Release this session's entire reservation after its final cleanup attempt."""
    with _LOCK:
        for key in tuple(_ACTIVE):
            remaining = [entry for entry in _ACTIVE[key] if entry[0] is not owner]
            if remaining:
                _ACTIVE[key] = remaining
            else:
                del _ACTIVE[key]
