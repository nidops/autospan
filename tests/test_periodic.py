from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

import pytest

from autospan.api import ComponentSpec, LifecycleError, PeriodicState, RuntimePlan
from autospan._core.evidence_store import LocalEvidenceStore
from autospan._core.periodic import RuntimePeriodicScheduler
from autospan._core.runtime import RuntimeSession


def _wait_until(predicate, timeout_s: float = 1.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("condition did not become true before timeout")
        time.sleep(0.005)


def test_nested_leases_share_one_periodic_worker(tmp_path) -> None:
    evidence = LocalEvidenceStore(tmp_path / "evidence", run_id="run")
    scheduler = RuntimePeriodicScheduler(evidence)
    lock = threading.Lock()
    calls = 0
    called = threading.Event()

    def callback() -> None:
        nonlocal calls
        with lock:
            calls += 1
        called.set()

    task = scheduler.create("heartbeat", interval_s=0.01, callback=callback)
    try:
        with task.lease():
            assert called.wait(1.0)
            _wait_until(lambda: task.state is PeriodicState.RUNNING)
            assert task.leases == 1
            with task.lease():
                assert task.leases == 2
            assert task.leases == 1
            with lock:
                before = calls
            _wait_until(lambda: task.stats.ticks > before)
        assert task.leases == 0
        assert task.state is PeriodicState.STOPPED
        stopped_ticks = task.stats.ticks
        time.sleep(0.03)
        assert task.stats.ticks == stopped_ticks
    finally:
        scheduler.close()
        evidence.close(outcome="passed")


def test_periodic_failure_is_observable_and_resettable(tmp_path) -> None:
    evidence = LocalEvidenceStore(tmp_path / "evidence", run_id="run")
    scheduler = RuntimePeriodicScheduler(evidence)
    fail = True
    succeeded = threading.Event()

    def callback() -> None:
        if fail:
            raise RuntimeError("tick exploded")
        succeeded.set()

    task = scheduler.create("fragile", interval_s=0.01, callback=callback)
    try:
        task.acquire()
        _wait_until(lambda: task.state is PeriodicState.FAILED)
        with pytest.raises(LifecycleError, match="tick exploded"):
            task.raise_if_failed()
        task.release()

        fail = False
        task.reset_failure()
        with task.lease():
            assert succeeded.wait(1.0)
        assert task.last_error is None
    finally:
        scheduler.close()
        evidence.close(outcome="passed")


def test_runtime_teardown_propagates_periodic_worker_failure(tmp_path) -> None:
    evidence = LocalEvidenceStore(tmp_path / "evidence", run_id="run")
    failed = threading.Event()

    def callback() -> None:
        failed.set()
        raise RuntimeError("background failure")

    try:
        with pytest.raises(LifecycleError, match="background failure"):
            with RuntimeSession(
                plan=RuntimePlan(components=()),
                factories={},
                evidence=evidence,
            ) as runtime:
                task = runtime.periodic.create("watch", interval_s=0.01, callback=callback)
                task.acquire()
                assert failed.wait(1.0)
                _wait_until(lambda: task.state is PeriodicState.FAILED)
    finally:
        evidence.close(outcome="failed")


class _PeriodicFactory:
    def __init__(self, log: list[str], ticked: threading.Event) -> None:
        self.log = log
        self.ticked = ticked

    @contextmanager
    def open(
        self,
        context: Any,
        dependencies: Mapping[str, object],
        config: Mapping[str, Any],
    ) -> Iterator[object]:
        del dependencies, config
        task = context.periodic.create(
            "component-heartbeat",
            interval_s=0.01,
            callback=lambda: self.ticked.set(),
        )
        task.acquire()
        self.log.append("component-open")
        try:
            yield task
        finally:
            self.log.append(f"component-close:{task.state.value}")


def test_runtime_stops_periodic_work_before_component_teardown(tmp_path) -> None:
    evidence = LocalEvidenceStore(tmp_path / "evidence", run_id="run")
    log: list[str] = []
    ticked = threading.Event()
    plan = RuntimePlan(components=(ComponentSpec("device", "device"),))
    try:
        with RuntimeSession(
            plan=plan,
            factories={"device": _PeriodicFactory(log, ticked)},
            evidence=evidence,
        ) as runtime:
            assert ticked.wait(1.0)
            assert runtime.periodic.get("component-heartbeat") is runtime.get("device")
    finally:
        evidence.close(outcome="passed")
    assert log == ["component-open", "component-close:closed"]


def test_periodic_events_keep_test_correlation(tmp_path) -> None:
    evidence = LocalEvidenceStore(tmp_path / "evidence", run_id="run")
    scheduler = RuntimePeriodicScheduler(evidence)
    called = threading.Event()
    try:
        with evidence.scope(test_id="scenario::case"):
            task = scheduler.create("correlated", interval_s=0.01, callback=called.set)
            with task.lease():
                assert called.wait(1.0)
        scheduler.close()
    finally:
        evidence.close(outcome="passed")

    events = [
        json.loads(line)
        for line in (tmp_path / "evidence" / "run" / "events.jsonl").read_text().splitlines()
    ]
    periodic = [item for item in events if item["name"].startswith("periodic_")]
    assert periodic
    assert any(item["test_id"] == "scenario::case" for item in periodic)


def test_scheduler_rejects_duplicate_task_names(tmp_path) -> None:
    evidence = LocalEvidenceStore(tmp_path / "evidence", run_id="run")
    scheduler = RuntimePeriodicScheduler(evidence)
    try:
        scheduler.create("same", interval_s=1.0, callback=lambda: None)
        with pytest.raises(LifecycleError, match="already exists"):
            scheduler.create("same", interval_s=1.0, callback=lambda: None)
    finally:
        scheduler.close()
        evidence.close(outcome="passed")


class _OutcomeFactory:
    def __init__(self, outcomes: list[str | None], failed: threading.Event) -> None:
        self.outcomes = outcomes
        self.failed = failed

    @contextmanager
    def open(
        self,
        context: Any,
        dependencies: Mapping[str, object],
        config: Mapping[str, Any],
    ) -> Iterator[object]:
        del dependencies, config

        def callback() -> None:
            self.failed.set()
            raise RuntimeError("periodic failure before component close")

        task = context.periodic.create("outcome-watch", interval_s=0.01, callback=callback)
        task.acquire()
        try:
            yield object()
        finally:
            self.outcomes.append(context.outcome)


def test_periodic_failure_marks_outcome_failed_before_component_cleanup(tmp_path) -> None:
    evidence = LocalEvidenceStore(tmp_path / "evidence", run_id="run")
    failed = threading.Event()
    outcomes: list[str | None] = []
    plan = RuntimePlan(components=(ComponentSpec("device", "device"),))
    try:
        with pytest.raises(LifecycleError, match="periodic failure before component close"):
            with RuntimeSession(
                plan=plan,
                factories={"device": _OutcomeFactory(outcomes, failed)},
                evidence=evidence,
            ):
                assert failed.wait(1.0)
        assert outcomes == ["failed"]
    finally:
        evidence.close(outcome="failed")


def test_startup_timeout_waits_for_first_healthy_tick(tmp_path) -> None:
    evidence = LocalEvidenceStore(tmp_path / "evidence", run_id="run")
    scheduler = RuntimePeriodicScheduler(evidence)
    called = threading.Event()
    task = scheduler.create("ready", interval_s=10.0, callback=called.set)
    try:
        assert task.acquire(startup_timeout_s=1.0) == 1
        assert called.is_set()
        assert task.stats.ticks == 1
        task.release()
    finally:
        scheduler.close()
        evidence.close(outcome="passed")


def test_startup_wait_surfaces_first_callback_failure(tmp_path) -> None:
    evidence = LocalEvidenceStore(tmp_path / "evidence", run_id="run")
    scheduler = RuntimePeriodicScheduler(evidence)

    def callback() -> None:
        raise RuntimeError("not ready")

    task = scheduler.create("not-ready", interval_s=1.0, callback=callback)
    try:
        with pytest.raises(LifecycleError, match="failed during startup: not ready"):
            task.acquire(startup_timeout_s=1.0)
        assert task.leases == 0
        assert task.state is PeriodicState.FAILED
    finally:
        with pytest.raises(LifecycleError, match="not ready"):
            scheduler.close()
        evidence.close(outcome="failed")


def test_startup_timeout_must_be_positive(tmp_path) -> None:
    evidence = LocalEvidenceStore(tmp_path / "evidence", run_id="run")
    scheduler = RuntimePeriodicScheduler(evidence)
    task = scheduler.create("invalid-timeout", interval_s=1.0, callback=lambda: None)
    try:
        with pytest.raises(ValueError, match="startup_timeout_s"):
            task.acquire(startup_timeout_s=0)
        assert task.leases == 0
    finally:
        scheduler.close()
        evidence.close(outcome="passed")


def test_startup_timeout_stops_worker_before_allowing_reuse(tmp_path) -> None:
    evidence = LocalEvidenceStore(tmp_path / "evidence", run_id="run")
    scheduler = RuntimePeriodicScheduler(evidence)
    entered = threading.Event()
    active_lock = threading.Lock()
    active = 0
    maximum_active = 0

    def callback() -> None:
        nonlocal active, maximum_active
        with active_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        entered.set()
        time.sleep(0.05)
        with active_lock:
            active -= 1

    task = scheduler.create("blocked", interval_s=0.01, callback=callback)
    try:
        with pytest.raises(LifecycleError, match="did not complete its first callback"):
            task.acquire(startup_timeout_s=0.01)
        assert entered.is_set()
        assert task.leases == 0
        assert task.state is PeriodicState.STOPPED
        assert maximum_active == 1

        with task.lease(startup_timeout_s=0.2):
            assert task.stats.ticks >= 2
        assert maximum_active == 1
    finally:
        scheduler.close()
        evidence.close(outcome="passed")


def test_lease_surfaces_failure_that_happens_while_body_runs(tmp_path) -> None:
    evidence = LocalEvidenceStore(tmp_path / "evidence", run_id="run")
    scheduler = RuntimePeriodicScheduler(evidence)
    failed = threading.Event()

    def callback() -> None:
        failed.set()
        raise RuntimeError("lease worker failed")

    task = scheduler.create("lease-failure", interval_s=0.01, callback=callback)
    try:
        with pytest.raises(LifecycleError, match="lease worker failed"):
            with task.lease():
                assert failed.wait(1.0)
                _wait_until(lambda: task.state is PeriodicState.FAILED)
        assert task.leases == 0
    finally:
        with pytest.raises(LifecycleError, match="lease worker failed"):
            scheduler.close()
        evidence.close(outcome="failed")


class _LeasedPeriodicFactory:
    def __init__(self, log: list[str], ticked: threading.Event) -> None:
        self.log = log
        self.ticked = ticked

    @contextmanager
    def open(
        self,
        context: Any,
        dependencies: Mapping[str, object],
        config: Mapping[str, Any],
    ) -> Iterator[object]:
        del dependencies, config
        task = context.periodic.create(
            "leased-component-heartbeat",
            interval_s=0.01,
            callback=self.ticked.set,
        )
        with task.lease(startup_timeout_s=1.0):
            self.log.append("component-open")
            try:
                yield task
            finally:
                self.log.append(f"component-close:{task.state.value}")


def test_runtime_can_stop_task_before_factory_lease_unwinds(tmp_path) -> None:
    evidence = LocalEvidenceStore(tmp_path / "evidence", run_id="run")
    log: list[str] = []
    ticked = threading.Event()
    plan = RuntimePlan(components=(ComponentSpec("device", "device"),))
    try:
        with RuntimeSession(
            plan=plan,
            factories={"device": _LeasedPeriodicFactory(log, ticked)},
            evidence=evidence,
        ):
            assert ticked.wait(1.0)
    finally:
        evidence.close(outcome="passed")
    assert log == ["component-open", "component-close:closed"]


def test_scheduler_closes_every_worker_when_stop_evidence_fails(tmp_path, monkeypatch) -> None:
    evidence = LocalEvidenceStore(tmp_path / "evidence")
    original_event = evidence.event
    scheduler = RuntimePeriodicScheduler(evidence)
    tasks = [scheduler.create(name, interval_s=1, callback=lambda: None) for name in ("a", "b")]
    for task in tasks:
        task.acquire(startup_timeout_s=1)

    def broken_event(name, **fields):
        if name == "periodic_stopped" and fields.get("task") == "b":
            raise OSError("stop evidence failed")
        original_event(name, **fields)

    monkeypatch.setattr(evidence, "event", broken_event)
    try:
        with pytest.raises(OSError, match="stop evidence failed"):
            scheduler.close()
        assert all(task.state is PeriodicState.CLOSED for task in tasks)
        assert not any(
            t.name in {"autospan-periodic-a", "autospan-periodic-b"} for t in threading.enumerate()
        )
    finally:
        monkeypatch.setattr(evidence, "event", original_event)
        for task in tasks:
            task.close()
        evidence.close(outcome="failed")


@pytest.mark.parametrize("reporting_fails", [False, True])
def test_slow_worker_stops_before_component_and_evidence_close(
    tmp_path, monkeypatch, reporting_fails
):
    evidence = LocalEvidenceStore(tmp_path / "evidence")
    entered = threading.Event()
    release = threading.Event()
    timed_join = threading.Event()
    closed = threading.Event()
    log = []
    errors = []
    original_join = threading.Thread.join
    original_event = evidence.event

    def observed_event(name, **fields):
        if name == "periodic_failed" and reporting_fails:
            raise OSError("slow shutdown evidence failed")
        original_event(name, **fields)

    monkeypatch.setattr(evidence, "event", observed_event)

    def observed_join(thread, timeout=None):
        if thread.name == "autospan-periodic-blocked" and timeout is not None:
            timed_join.set()
            return original_join(thread, timeout=0)
        return original_join(thread, timeout=timeout)

    monkeypatch.setattr(threading.Thread, "join", observed_join)

    def callback():
        entered.set()
        assert release.wait(5)
        log.append("worker-stopped")

    class Factory:
        @contextmanager
        def open(self, context, dependencies, config):
            context.periodic.create("blocked", interval_s=0.01, callback=callback).acquire()
            try:
                yield object()
            finally:
                log.append("component-closed")

    session = RuntimeSession(
        plan=RuntimePlan(components=(ComponentSpec("bench", "bench"),)),
        factories={"bench": Factory()},
        evidence=evidence,
    )
    session.__enter__()
    assert entered.wait(1)

    def shutdown():
        try:
            session.__exit__(None, None, None)
        except BaseException as exc:
            errors.append(exc)
        finally:
            closed.set()

    closer = threading.Thread(target=shutdown)
    closer.start()
    try:
        assert timed_join.wait(1)
        assert not closed.wait(0.05)
        assert log == []
    finally:
        release.set()
        closer.join(2)
        assert not closer.is_alive()
        evidence.close(outcome="failed")
    assert log == ["worker-stopped", "component-closed"]
    assert len(errors) == 1
    assert isinstance(errors[0], LifecycleError)
    if not reporting_fails:
        assert "did not stop cleanly" in str(errors[0])
