"""Deterministic runtime acquisition and cleanup."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from contextlib import ExitStack
from types import TracebackType
from typing import Any, Literal

from autospan.api.component import RuntimePlan
from autospan.api.errors import GateError, LifecycleError
from autospan.api.runtime import Runtime
from autospan._core.evidence_store import LocalEvidenceStore
from autospan._core.handover import HandoverCoordinator
from autospan._core.periodic import RuntimePeriodicScheduler

OUTCOMES = frozenset({"passed", "failed", "skipped"})


def _cleanup_failures(
    error: BaseException, original: BaseException | None
) -> Iterator[BaseException]:
    """Retain ExitStack's chained cleanup failures before re-raising the original."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and current is not original and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__context__


class _ComponentContext:
    """Narrow per-runtime context handed to component factories."""

    __slots__ = ("_session",)

    def __init__(self, session: "RuntimeSession") -> None:
        self._session = session

    @property
    def evidence(self) -> LocalEvidenceStore:
        return self._session.evidence

    @property
    def periodic(self) -> RuntimePeriodicScheduler:
        return self._session.periodic

    @property
    def outcome(self) -> str | None:
        return self._session.outcome

    def handover(self, resource_key: str) -> Any:
        return self._session.handover(resource_key)


class RuntimeSession:
    """Own one acquisition of the component graph for a frontend/fixture scope.

    ``enabled_gates=None`` means the frontend does not restrict acquisition
    (internal callers only). An iterable restricts acquisition to components
    whose ``gates`` are all enabled; hosted frontends pass an explicit set.
    Startup failures roll back; shutdown joins periodic work before components
    close. The caller owns evidence finalization after cleanup.
    """

    def __init__(
        self,
        *,
        plan: RuntimePlan,
        factories: Mapping[str, object],
        evidence: LocalEvidenceStore,
        enabled_gates: Iterable[str] | None = None,
    ) -> None:
        self._plan = plan
        self._factories = factories
        self.evidence = evidence
        self._enabled_gates = None if enabled_gates is None else frozenset(enabled_gates)
        self._stack = ExitStack()
        self._runtime: Runtime | None = None
        self._outcome: str | None = None
        self._instances: dict[str, object] = {}
        self._coordinator = HandoverCoordinator(plan, self._instances, evidence)
        self._periodic: RuntimePeriodicScheduler | None = None

    @property
    def periodic(self) -> RuntimePeriodicScheduler:
        scheduler = self._periodic
        if scheduler is None:
            raise LifecycleError("Runtime periodic scheduler is not active")
        return scheduler

    @property
    def outcome(self) -> str | None:
        return self._outcome

    def set_outcome(self, outcome: str | None) -> None:
        """Record the owning scope's verdict before teardown (see ``ComponentContext.outcome``)."""
        if outcome is not None and outcome not in OUTCOMES:
            raise ValueError(
                f"Unknown runtime outcome {outcome!r}; expected one of {sorted(OUTCOMES)}"
            )
        self._outcome = outcome

    def handover(self, resource_key: str) -> Any:
        return self._coordinator.handover(resource_key)

    def missing_gates(self) -> dict[str, tuple[str, ...]]:
        """Return ``component -> missing gates`` for the configured gate restriction."""
        if self._enabled_gates is None:
            return {}
        missing: dict[str, tuple[str, ...]] = {}
        for spec in self._plan.components:
            absent = tuple(gate for gate in spec.gates if gate not in self._enabled_gates)
            if absent:
                missing[spec.name] = absent
        return missing

    def _record_failure(self, name: str, error: BaseException, **attributes: Any) -> None:
        """A diagnostic write must not interrupt resource cleanup."""
        try:
            self.evidence.event(name, error=repr(error), **attributes)
        except BaseException as reporting_error:
            error.add_note(f"Evidence event {name!r} failed: {reporting_error!r}")

    def __enter__(self) -> Runtime:
        missing = self.missing_gates()
        if missing:
            gates = tuple(sorted({gate for values in missing.values() for gate in values}))
            flags = " ".join("--" + gate.replace("_", "-") for gate in gates)
            owners = ", ".join(sorted(missing))
            self.evidence.event("runtime_gated", components=sorted(missing), gates=list(gates))
            raise GateError(f"runtime component(s) {owners} require {flags}", missing=gates)

        instances = self._instances
        instances.clear()
        self._periodic = RuntimePeriodicScheduler(self.evidence)
        context = _ComponentContext(self)
        self._outcome = None
        try:
            self.evidence.event("runtime_starting")
            for spec in self._plan.components:
                factory: Any = self._factories.get(spec.type_id)
                if factory is None:
                    raise LifecycleError(
                        f"No factory is registered for component type {spec.type_id!r}"
                    )
                dependencies = {name: instances[name] for name in spec.dependencies}
                self.evidence.event(
                    "component_opening",
                    component=spec.name,
                    type_id=spec.type_id,
                )
                manager = factory.open(context, dependencies, spec.config)
                instance = self._stack.enter_context(manager)
                instances[spec.name] = instance
                self.evidence.event(
                    "component_opened",
                    component=spec.name,
                    type_id=spec.type_id,
                )
            self._runtime = Runtime(
                instances,
                self.evidence,
                self._coordinator.handover,
                periodic=self.periodic,
            )
            self.evidence.event("runtime_started", components=list(self._runtime.names()))
        except BaseException as exc:
            # BaseException: Ctrl+C during a slow hardware open must still roll back.
            self._outcome = "failed"
            self._record_failure("runtime_start_failed", exc)
            try:
                if self._periodic is not None:
                    self._periodic.close()
            except BaseException as periodic_exc:
                exc.add_note(f"Periodic startup rollback failed: {periodic_exc!r}")
                self._record_failure(
                    "runtime_cleanup_failed",
                    periodic_exc,
                    phase="startup_periodic",
                )
            try:
                self._stack.__exit__(type(exc), exc, exc.__traceback__)
            except BaseException as cleanup_exc:
                for error in _cleanup_failures(cleanup_exc, exc):
                    exc.add_note(f"Component startup rollback failed: {error!r}")
                    self._record_failure(
                        "runtime_cleanup_failed",
                        error,
                        phase="startup_rollback",
                    )
            self._stack = ExitStack()
            self._runtime = None
            self._periodic = None
            instances.clear()
            raise
        return self._runtime

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        if exc is not None and self._outcome is None:
            self._outcome = "failed"
        cleanup_errors: list[BaseException] = []
        try:
            self.evidence.event(
                "runtime_stopping",
                error=repr(exc) if exc is not None else None,
                outcome=self._outcome,
            )
        except BaseException as reporting_error:
            cleanup_errors.append(reporting_error)
            self._outcome = "failed"
        try:
            if self._periodic is not None:
                self._periodic.close()
        except BaseException as periodic_exc:
            cleanup_errors.append(periodic_exc)
            self._outcome = "failed"
            self._record_failure("runtime_cleanup_failed", periodic_exc, phase="periodic")
        try:
            self._stack.__exit__(exc_type, exc, tb)
        except BaseException as close_exc:  # preserve original failure if there is one
            self._outcome = "failed"
            for error in _cleanup_failures(close_exc, exc):
                cleanup_errors.append(error)
                self._record_failure("runtime_cleanup_failed", error, phase="components")
        else:
            if not cleanup_errors:
                try:
                    self.evidence.event("runtime_stopped")
                except BaseException as reporting_error:
                    cleanup_errors.append(reporting_error)
                    self._outcome = "failed"
        finally:
            self._stack = ExitStack()
            self._runtime = None
            self._periodic = None

        if exc is not None:
            for error in cleanup_errors:
                exc.add_note(f"Runtime cleanup failed: {error!r}")

        if exc is None and cleanup_errors:
            if len(cleanup_errors) == 1:
                error = cleanup_errors[0]
                raise LifecycleError(f"Runtime teardown failed: {error}") from error
            details = "; ".join(str(item) for item in cleanup_errors)
            raise LifecycleError(f"Runtime teardown failed: {details}") from cleanup_errors[0]
        return False
