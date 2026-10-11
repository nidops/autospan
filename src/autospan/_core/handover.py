"""Cooperative hand-over of a shared physical resource to an external tool."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from autospan.api.component import ResourceYield, RuntimePlan
from autospan.api.errors import LifecycleError


class HandoverCoordinator:
    """Release every live holder of a resource key, then take it back.

    Holders are the components whose plan claims name the key. They must implement
    :class:`~autospan.api.component.ResourceYield`. Release runs dependents first;
    reacquisition runs in the opposite order, even when the guarded block fails.
    """

    def __init__(
        self,
        plan: RuntimePlan,
        instances: Mapping[str, object],
        evidence: Any,
        on_recovery_failure: Callable[[str, list[BaseException]], None] | None = None,
    ) -> None:
        self._plan = plan
        self._instances = instances
        self._evidence = evidence
        self._active: set[str] = set()
        self._on_recovery_failure = on_recovery_failure

    def _holders(self, key: str) -> list[str]:
        known = {claim.key for spec in self._plan.components for claim in spec.claims}
        if key not in known:
            rendered = ", ".join(sorted(known)) or "<none>"
            raise LifecycleError(f"Unknown resource {key!r}; claimed resources: {rendered}")
        return [
            spec.name
            for spec in self._plan.components
            if any(claim.key == key for claim in spec.claims) and spec.name in self._instances
        ]

    @contextmanager
    def handover(self, key: str) -> Iterator[None]:
        if key in self._active:
            raise LifecycleError(f"Resource {key!r} is already handed over")
        holders = self._holders(key)
        blocked = [n for n in holders if not isinstance(self._instances[n], ResourceYield)]
        if blocked:
            raise LifecycleError(
                f"Resource {key!r} cannot be handed over; component(s) {', '.join(blocked)} "
                f"do not implement ResourceYield"
            )
        released: list[str] = []

        def reacquire_all() -> list[BaseException]:
            errors: list[BaseException] = []
            for name in reversed(released):
                try:
                    self._instances[name].reacquire()  # type: ignore[attr-defined]
                    self._evidence.event("resource_reacquired", resource=key, component=name)
                except BaseException as exc:
                    errors.append(exc)
                    try:
                        self._evidence.event(
                            "resource_reacquire_failed",
                            resource=key,
                            component=name,
                            error=repr(exc),
                        )
                    except BaseException as reporting_error:
                        exc.add_note(f"Reacquisition evidence failed: {reporting_error!r}")
            released.clear()
            return errors

        self._active.add(key)
        body_error: BaseException | None = None
        try:
            for name in reversed(holders):
                # A release can change hardware state before raising. Track it first
                # so the recovery path also attempts to reacquire that holder.
                # ResourceYield.reacquire() must be idempotent in either case.
                released.append(name)
                self._instances[name].release()  # type: ignore[attr-defined]
                self._evidence.event("resource_released", resource=key, component=name)
            yield
        except BaseException as exc:
            body_error = exc
            raise
        finally:
            try:
                errors = reacquire_all()
                if errors and self._on_recovery_failure is not None:
                    self._on_recovery_failure(key, errors)
                if body_error is not None:
                    for error in errors:
                        body_error.add_note(f"Resource reacquisition failed: {error!r}")
                elif errors:
                    failure = LifecycleError(
                        f"Resource {key!r} could not be reacquired: {errors[0]}"
                    )
                    for error in errors[1:]:
                        failure.add_note(f"Resource reacquisition also failed: {error!r}")
                    raise failure from errors[0]
            finally:
                self._active.discard(key)
