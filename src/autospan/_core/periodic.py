"""Runtime-owned, protocol-neutral periodic worker implementation."""

from __future__ import annotations

import math
from collections.abc import Callable
from contextlib import contextmanager
from threading import Event, RLock, Thread, current_thread
from time import monotonic
from typing import Iterator

from autospan.api.errors import LifecycleError
from autospan.api.evidence import Evidence, EvidenceContext
from autospan.api.periodic import PeriodicState, PeriodicStats, PeriodicTask

_SOURCE = "autospan.periodic"


class _ThreadPeriodicTask:
    """One leased periodic callback supervised by an AutoSpan runtime."""

    def __init__(
        self,
        *,
        name: str,
        interval_s: float,
        callback: Callable[[], None],
        run_immediately: bool,
        evidence: Evidence,
    ) -> None:
        label = str(name).strip()
        if not label:
            raise ValueError("Periodic task name must not be empty")
        interval = float(interval_s)
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("Periodic interval_s must be a finite positive number")
        if not callable(callback):
            raise TypeError("Periodic callback must be callable")

        self._name = label
        self._interval_s = interval
        self._callback = callback
        self._run_immediately = bool(run_immediately)
        self._evidence = evidence
        self._lock = RLock()
        self._stop = Event()
        self._startup_done = Event()
        self._thread: Thread | None = None
        self._state = PeriodicState.STOPPED
        self._leases = 0
        self._closed = False
        self._ticks = 0
        self._overruns = 0
        self._last_duration_s: float | None = None
        self._last_error: BaseException | None = None
        self._run_context: EvidenceContext | None = None
        self._evidence.event(
            "periodic_created",
            source=_SOURCE,
            task=self._name,
            interval_s=self._interval_s,
            run_immediately=self._run_immediately,
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def interval_s(self) -> float:
        return self._interval_s

    @property
    def state(self) -> PeriodicState:
        with self._lock:
            return self._state

    @property
    def leases(self) -> int:
        with self._lock:
            return self._leases

    @property
    def stats(self) -> PeriodicStats:
        with self._lock:
            return PeriodicStats(
                ticks=self._ticks,
                overruns=self._overruns,
                last_duration_s=self._last_duration_s,
            )

    @property
    def last_error(self) -> BaseException | None:
        with self._lock:
            return self._last_error

    def acquire(self, *, startup_timeout_s: float | None = None) -> int:
        if startup_timeout_s is not None:
            timeout = float(startup_timeout_s)
            if not math.isfinite(timeout) or timeout <= 0:
                raise ValueError("startup_timeout_s must be a finite positive number")
        else:
            timeout = None

        with self._lock:
            if self._closed:
                raise LifecycleError(f"Periodic task {self._name!r} is closed")
            if self._last_error is not None:
                raise LifecycleError(
                    f"Periodic task {self._name!r} is failed; call reset_failure() before reuse"
                ) from self._last_error
            self._leases += 1
            leases = self._leases
            if leases == 1:
                self._stop.clear()
                self._startup_done.clear()
                self._state = PeriodicState.STARTING
                self._run_context = self._evidence.current_context
                thread = Thread(
                    target=self._run,
                    name=f"autospan-periodic-{self._name}",
                    daemon=True,
                )
                self._thread = thread
                self._evidence.event(
                    "periodic_started",
                    source=_SOURCE,
                    task=self._name,
                    interval_s=self._interval_s,
                )
                thread.start()

        if timeout is not None:
            if not self._startup_done.wait(timeout):
                self.release()
                raise LifecycleError(
                    f"Periodic task {self._name!r} did not complete its first callback "
                    f"within {timeout:g}s"
                )
            error = self.last_error
            if error is not None:
                self.release()
                raise LifecycleError(
                    f"Periodic task {self._name!r} failed during startup: {error}"
                ) from error
        return leases

    def release(self) -> int:
        thread: Thread | None = None
        with self._lock:
            if self._leases <= 0:
                if self._closed:
                    return 0
                raise LifecycleError(f"Periodic task {self._name!r} has no active lease")
            self._leases -= 1
            leases = self._leases
            if leases == 0:
                if self._last_error is None:
                    self._state = PeriodicState.STOPPING
                self._stop.set()
                thread = self._thread
        if thread is not None and thread is not current_thread():
            self._join_worker(thread)
        if leases == 0:
            self._finish_stop()
        return leases

    @contextmanager
    def lease(self, *, startup_timeout_s: float | None = None) -> Iterator[PeriodicTask]:
        self.acquire(startup_timeout_s=startup_timeout_s)
        body_failed = False
        try:
            yield self
        except BaseException:
            body_failed = True
            raise
        finally:
            try:
                with self._lock:
                    runtime_closed = self._closed
                if not body_failed and not runtime_closed:
                    self.raise_if_failed()
            finally:
                self.release()

    def raise_if_failed(self) -> None:
        error = self.last_error
        if error is not None:
            raise LifecycleError(f"Periodic task {self._name!r} failed: {error}") from error

    def reset_failure(self) -> None:
        with self._lock:
            if self._closed:
                raise LifecycleError(f"Periodic task {self._name!r} is closed")
            if self._leases:
                raise LifecycleError(
                    f"Periodic task {self._name!r} cannot reset failure with active leases"
                )
            if self._thread is not None and self._thread.is_alive():
                raise LifecycleError(f"Periodic task {self._name!r} worker is still running")
            self._last_error = None
            self._state = PeriodicState.STOPPED
            self._evidence.event("periodic_failure_reset", source=_SOURCE, task=self._name)

    def close(self) -> None:
        thread: Thread | None
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._leases = 0
            if self._last_error is None and self._thread is not None:
                self._state = PeriodicState.STOPPING
            self._stop.set()
            thread = self._thread
        if thread is not None and thread is not current_thread():
            self._join_worker(thread)
        self._finish_stop(closing=True)

    def _join_worker(self, thread: Thread) -> None:
        thread.join(timeout=max(1.0, self._interval_s * 3.0))
        if not thread.is_alive():
            return
        error = LifecycleError(f"Periodic task {self._name!r} did not stop cleanly")
        with self._lock:
            if self._last_error is None:
                self._last_error = error
            self._state = PeriodicState.FAILED
        try:
            self._evidence.event(
                "periodic_failed",
                source=_SOURCE,
                task=self._name,
                error=repr(error),
            )
        finally:
            # A timeout reports slow shutdown; it cannot revoke a callback's
            # access to live components. Keep ownership until the worker exits.
            thread.join()
        raise error

    def _finish_stop(self, *, closing: bool = False) -> None:
        with self._lock:
            thread = self._thread
            if thread is not None and thread.is_alive():
                return
            self._thread = None
            if closing:
                self._state = PeriodicState.CLOSED
            elif self._last_error is not None:
                self._state = PeriodicState.FAILED
            else:
                self._state = PeriodicState.STOPPED
            stats = PeriodicStats(
                ticks=self._ticks,
                overruns=self._overruns,
                last_duration_s=self._last_duration_s,
            )
        self._evidence.event(
            "periodic_stopped",
            source=_SOURCE,
            task=self._name,
            ticks=stats.ticks,
            overruns=stats.overruns,
            failed=self._last_error is not None,
        )

    def _run(self) -> None:
        context = self._run_context
        if context is None:  # defensive; acquire always sets it before starting the worker
            context = self._evidence.current_context
        with self._evidence.bind_context(context):
            next_deadline = monotonic()
            if not self._run_immediately:
                next_deadline += self._interval_s
            while not self._stop.is_set():
                delay = next_deadline - monotonic()
                if delay > 0 and self._stop.wait(delay):
                    break
                if self._stop.is_set():
                    break

                started = monotonic()
                error: BaseException | None = None
                try:
                    self._callback()
                except BaseException as exc:  # worker failures must be observed by the owner
                    error = exc
                finished = monotonic()

                with self._lock:
                    self._ticks += 1
                    self._last_duration_s = finished - started
                    if error is not None:
                        self._last_error = error
                        self._state = PeriodicState.FAILED
                    elif self._state is PeriodicState.STARTING:
                        self._state = PeriodicState.RUNNING
                self._startup_done.set()

                if error is not None:
                    self._evidence.event(
                        "periodic_failed",
                        source=_SOURCE,
                        task=self._name,
                        error=repr(error),
                    )
                    self._stop.set()
                    break

                next_deadline += self._interval_s
                now = monotonic()
                if next_deadline < now:
                    missed = int((now - next_deadline) // self._interval_s) + 1
                    with self._lock:
                        self._overruns += missed
                    next_deadline += missed * self._interval_s


class RuntimePeriodicScheduler:
    """Periodic registry whose lifetime is exactly one active RuntimeSession."""

    def __init__(self, evidence: Evidence) -> None:
        self._evidence = evidence
        self._tasks: dict[str, _ThreadPeriodicTask] = {}
        self._lock = RLock()
        self._closed = False

    def create(
        self,
        name: str,
        *,
        interval_s: float,
        callback: Callable[[], None],
        run_immediately: bool = True,
    ) -> PeriodicTask:
        label = str(name).strip()
        with self._lock:
            if self._closed:
                raise LifecycleError("Periodic scheduler is closed")
            if label in self._tasks:
                raise LifecycleError(f"Periodic task {label!r} already exists in this runtime")
            task = _ThreadPeriodicTask(
                name=label,
                interval_s=interval_s,
                callback=callback,
                run_immediately=run_immediately,
                evidence=self._evidence,
            )
            self._tasks[label] = task
            return task

    def get(self, name: str) -> PeriodicTask:
        with self._lock:
            try:
                return self._tasks[name]
            except KeyError as exc:
                available = ", ".join(self._tasks) or "<none>"
                raise KeyError(f"Unknown periodic task {name!r}; available: {available}") from exc

    def names(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._tasks)

    def raise_if_failed(self) -> None:
        with self._lock:
            tasks = tuple(self._tasks.values())
        failures: list[LifecycleError] = []
        for task in tasks:
            try:
                task.raise_if_failed()
            except LifecycleError as exc:
                failures.append(exc)
        if not failures:
            return
        if len(failures) == 1:
            raise failures[0]
        details = "; ".join(str(item) for item in failures)
        raise LifecycleError(f"Multiple periodic tasks failed: {details}")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            tasks = tuple(reversed(tuple(self._tasks.values())))
        errors: list[BaseException] = []
        for task in tasks:
            try:
                task.close()
            except LifecycleError:
                # close() records stop failures on the task; continue so every
                # worker gets a shutdown attempt, then report the aggregate.
                pass
            except BaseException as exc:
                errors.append(exc)
        try:
            self.raise_if_failed()
        except BaseException as exc:
            errors.append(exc)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("Periodic scheduler cleanup failed", errors)
