"""Protocol-neutral periodic execution contracts."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol


class PeriodicState(str, Enum):
    """Lifecycle state of one periodic task."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class PeriodicStats:
    """Small timing summary for one periodic task.

    ``ticks`` counts callback invocations. ``overruns`` counts schedule slots
    skipped because a callback completed after its next deadline. AutoSpan does
    not attempt to catch up missed slots with a burst of callbacks.
    """

    ticks: int = 0
    overruns: int = 0
    last_duration_s: float | None = None


class PeriodicTask(Protocol):
    """One runtime-owned periodic callback.

    Work runs only while at least one lease is active. Nested users can safely
    share a task: the first lease starts it and the final lease stops it.
    Callbacks must return so shutdown can join them before components close.
    Scheduling and startup deadlines are host-side checks, not real-time bounds.
    """

    @property
    def name(self) -> str: ...

    @property
    def interval_s(self) -> float: ...

    @property
    def state(self) -> PeriodicState: ...

    @property
    def leases(self) -> int: ...

    @property
    def stats(self) -> PeriodicStats: ...

    @property
    def last_error(self) -> BaseException | None: ...

    def acquire(self, *, startup_timeout_s: float | None = None) -> int:
        """Acquire one lease and optionally wait for the first callback to finish.

        ``startup_timeout_s`` is useful when the caller must know that the
        periodic operation has completed one healthy tick before continuing.
        While the worker is still starting, nested leases using a startup
        timeout wait for that same first callback rather than creating another
        worker.
        Returns the lease count. A failure/timeout releases this lease before
        raising ``LifecycleError``; joining a slow callback can exceed the timeout.
        """
        ...

    def release(self) -> int:
        """Return the remaining count; the final release waits for the worker to exit.

        A slow stop is reported as ``LifecycleError`` after joining. A callback
        failure is observed through ``raise_if_failed()``, a lease, or shutdown.
        """
        ...

    def lease(
        self, *, startup_timeout_s: float | None = None
    ) -> AbstractContextManager["PeriodicTask"]:
        """Acquire/release a lease; cleanup failures annotate an existing exception."""
        ...

    def raise_if_failed(self) -> None:
        """Raise a lifecycle error if the callback failed in its worker thread."""
        ...

    def reset_failure(self) -> None:
        """Clear a previous callback failure after every lease has been released."""
        ...


class PeriodicScheduler(Protocol):
    """Runtime-owned factory/registry for protocol-neutral periodic work."""

    def create(
        self,
        name: str,
        *,
        interval_s: float,
        callback: Callable[[], None],
        run_immediately: bool = True,
    ) -> PeriodicTask:
        """Create one named task without starting it."""
        ...

    def get(self, name: str) -> PeriodicTask:
        """Return a previously created task."""
        ...

    def names(self) -> tuple[str, ...]:
        """Return task names in deterministic creation order."""
        ...

    def raise_if_failed(self) -> None:
        """Raise if any registered task has failed."""
        ...
