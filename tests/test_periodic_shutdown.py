"""Periodic shutdown keeps live workers' component ownership intact."""

from contextlib import contextmanager
import threading

import pytest

from autospan.api import ComponentSpec, LifecycleError, ResourceClaim, RuntimePlan
from autospan._core.evidence_store import LocalEvidenceStore
from autospan._core.periodic import RuntimePeriodicScheduler
from autospan._core.runtime import RuntimeSession


@pytest.mark.parametrize("owner", ["task", "scheduler", "session"])
def test_worker_cannot_close_its_owner(tmp_path, owner):
    evidence = LocalEvidenceStore(tmp_path / "evidence")
    session = RuntimeSession(plan=RuntimePlan(()), factories={}, evidence=evidence)
    runtime = session.__enter__()
    ready, rejected, finish = threading.Event(), threading.Event(), threading.Event()
    errors = []

    def callback():
        assert ready.wait(5)
        try:
            close = {
                "task": task.close,
                "scheduler": runtime.periodic.close,
                "session": lambda: session.__exit__(None, None, None),
            }[owner]
            with pytest.raises(LifecycleError, match="worker|periodic callback"):
                close()
        except BaseException as exc:
            errors.append(exc)
        finally:
            rejected.set()
        assert finish.wait(5)

    task = runtime.periodic.create("self-close", interval_s=60, callback=callback)
    try:
        task.acquire()
        ready.set()
        assert rejected.wait(2)
        assert not errors
        assert task.leases == 1
        runtime.periodic.create("still-open", interval_s=60, callback=lambda: None)
    finally:
        finish.set()
        session.__exit__(None, None, None)
        evidence.close()


def test_interrupted_worker_join_keeps_components_and_claims_until_callback_returns(
    tmp_path, monkeypatch
):
    evidence = LocalEvidenceStore(tmp_path / "evidence")
    other_evidence = LocalEvidenceStore(tmp_path / "other")
    entered, finish, interrupted, closed = (threading.Event() for _ in range(4))
    component_closed = threading.Event()
    log, errors = [], []
    interruption = KeyboardInterrupt("worker join interrupted")
    original_join = threading.Thread.join

    def observed_join(thread, timeout=None):
        if thread.name == "autospan-periodic-interrupted" and not interrupted.is_set():
            interrupted.set()
            raise interruption
        return original_join(thread, timeout)

    monkeypatch.setattr(threading.Thread, "join", observed_join)

    def callback():
        entered.set()
        assert finish.wait(5)
        log.append("worker-returned")

    class Factory:
        @contextmanager
        def open(self, context, dependencies, config):
            context.periodic.create("interrupted", interval_s=60, callback=callback).acquire()
            try:
                yield object()
            finally:
                component_closed.set()
                log.append("component-closed")

    plan = RuntimePlan(
        (ComponentSpec("device", "device", claims=(ResourceClaim("periodic:device"),)),)
    )
    session = RuntimeSession(plan=plan, factories={"device": Factory()}, evidence=evidence)
    other = RuntimeSession(plan=plan, factories={}, evidence=other_evidence)
    session.__enter__()

    def shutdown():
        try:
            session.__exit__(None, None, None)
        except BaseException as exc:
            errors.append(exc)
        finally:
            closed.set()

    closer = threading.Thread(target=shutdown, daemon=True)
    try:
        assert entered.wait(2)
        closer.start()
        assert interrupted.wait(2)
        assert not component_closed.is_set()
        assert not closed.is_set()
        with pytest.raises(LifecycleError, match="already claimed"):
            other.__enter__()
    finally:
        finish.set()
        closer.join(2)
        assert not closer.is_alive()
        evidence.close()
    assert log == ["worker-returned", "component-closed"]
    assert len(errors) == 1
    assert isinstance(errors[0], LifecycleError)
    assert errors[0].__cause__ is interruption
    # Reaching factory lookup proves the interrupted runtime released its claim.
    try:
        with pytest.raises(LifecycleError, match="No factory"):
            other.__enter__()
    finally:
        other_evidence.close()


@pytest.mark.parametrize("owner", ["task", "scheduler"])
def test_concurrent_close_waits_for_an_already_closing_worker(tmp_path, monkeypatch, owner):
    evidence = LocalEvidenceStore(tmp_path / "evidence")
    scheduler = RuntimePeriodicScheduler(evidence)
    entered, finish = threading.Event(), threading.Event()
    joining = {name: threading.Event() for name in ("first", "second")}
    completed = {name: threading.Event() for name in joining}
    original_join = threading.Thread.join
    errors = []

    def observed_join(thread, timeout=None):
        if thread.name == "autospan-periodic-concurrent" and timeout is not None:
            joining[threading.current_thread().name].set()
        return original_join(thread, timeout)

    monkeypatch.setattr(threading.Thread, "join", observed_join)

    def callback():
        entered.set()
        assert finish.wait(5)

    task = scheduler.create("concurrent", interval_s=60, callback=callback)

    def shutdown():
        try:
            (task if owner == "task" else scheduler).close()
        except BaseException as exc:
            errors.append(exc)
        finally:
            completed[threading.current_thread().name].set()

    closers = [threading.Thread(target=shutdown, name=name, daemon=True) for name in joining]
    try:
        task.acquire()
        assert entered.wait(2)
        for closer in closers:
            closer.start()
            assert joining[closer.name].wait(2)
        assert not any(event.is_set() for event in completed.values())
    finally:
        finish.set()
        for closer in closers:
            closer.join(2)
            assert not closer.is_alive()
        scheduler.close()
        evidence.close()
    assert not errors


def test_scheduler_requests_every_worker_stop_before_joining(tmp_path, monkeypatch):
    evidence = LocalEvidenceStore(tmp_path / "evidence")
    scheduler = RuntimePeriodicScheduler(evidence)
    entered = [threading.Event(), threading.Event()]
    finish, joining = threading.Event(), threading.Event()
    original_join = threading.Thread.join
    errors = []

    def observed_join(thread, timeout=None):
        if thread.name.startswith("autospan-periodic-") and timeout is not None:
            joining.set()
        return original_join(thread, timeout)

    monkeypatch.setattr(threading.Thread, "join", observed_join)

    def callback(index):
        entered[index].set()
        assert finish.wait(5)

    tasks = [
        scheduler.create(str(index), interval_s=60, callback=lambda i=index: callback(i))
        for index in range(2)
    ]

    def shutdown():
        try:
            scheduler.close()
        except BaseException as exc:
            errors.append(exc)

    closer = threading.Thread(target=shutdown, daemon=True)
    try:
        for index, task in enumerate(tasks):
            task.acquire()
            assert entered[index].wait(2)
        closer.start()
        assert joining.wait(2)
        assert all(task.leases == 0 for task in tasks)
        for task in tasks:
            with pytest.raises(LifecycleError, match="closed"):
                task.acquire()
    finally:
        finish.set()
        closer.join(2)
        assert not closer.is_alive()
        evidence.close()
    assert not errors


def test_reacquisition_cannot_replace_a_still_stopping_worker(tmp_path, monkeypatch):
    evidence = LocalEvidenceStore(tmp_path / "evidence")
    scheduler = RuntimePeriodicScheduler(evidence)
    entered, finish, joining = threading.Event(), threading.Event(), threading.Event()
    original_join = threading.Thread.join
    errors = []

    def observed_join(thread, timeout=None):
        if thread.name == "autospan-periodic-restart" and timeout is not None:
            joining.set()
        return original_join(thread, timeout)

    monkeypatch.setattr(threading.Thread, "join", observed_join)

    def callback():
        entered.set()
        assert finish.wait(5)

    task = scheduler.create("restart", interval_s=60, callback=callback)

    def release():
        try:
            task.release()
        except BaseException as exc:
            errors.append(exc)

    releaser = threading.Thread(target=release, daemon=True)
    try:
        task.acquire()
        assert entered.wait(2)
        releaser.start()
        assert joining.wait(2)
        with pytest.raises(LifecycleError, match="still stopping"):
            task.acquire()
    finally:
        finish.set()
        releaser.join(2)
        assert not releaser.is_alive()
    try:
        assert not errors
        with task.lease(startup_timeout_s=2):
            assert task.leases == 1
    finally:
        scheduler.close()
        evidence.close()


def test_start_evidence_failure_stops_worker_and_allows_retry(tmp_path, monkeypatch):
    evidence = LocalEvidenceStore(tmp_path / "evidence")
    scheduler = RuntimePeriodicScheduler(evidence)
    original_event = evidence.event

    def event(name, **fields):
        if name == "periodic_started":
            raise KeyboardInterrupt("start reporting interrupted")
        original_event(name, **fields)

    task = scheduler.create("not-started", interval_s=60, callback=lambda: None)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(evidence, "event", event)
            with pytest.raises(KeyboardInterrupt, match="start reporting"):
                task.acquire()
        assert task.leases == 0
        with task.lease(startup_timeout_s=2):
            pass
    finally:
        scheduler.close()
        evidence.close()


def test_periodic_evidence_callbacks_can_inspect_scheduler_from_another_thread(
    tmp_path, monkeypatch
):
    evidence = LocalEvidenceStore(tmp_path / "evidence")
    scheduler = RuntimePeriodicScheduler(evidence)
    original_event = evidence.event
    observed = []

    def event(name, **fields):
        if name in {"periodic_created", "periodic_started", "periodic_failure_reset"}:
            inspected = threading.Event()
            errors = []

            def inspect():
                try:
                    scheduler.get(fields["task"]).state
                except BaseException as error:
                    errors.append(error)
                finally:
                    inspected.set()

            reader = threading.Thread(target=inspect, daemon=True)
            reader.start()
            assert inspected.wait(5), "evidence callback ran under a lifecycle lock"
            reader.join(5)
            assert not reader.is_alive()
            assert errors == []
            observed.append(name)
        original_event(name, **fields)

    monkeypatch.setattr(evidence, "event", event)
    failing = True

    def callback():
        if failing:
            raise ValueError("callback failed")

    try:
        task = scheduler.create("inspect", interval_s=60, callback=callback)
        with pytest.raises(LifecycleError, match="callback failed"):
            task.acquire(startup_timeout_s=2)
        failing = False
        task.reset_failure()
        with task.lease(startup_timeout_s=2):
            pass
        assert observed == [
            "periodic_created",
            "periodic_started",
            "periodic_failure_reset",
            "periodic_started",
        ]
    finally:
        scheduler.close()
        evidence.close()


def test_shutdown_retains_lifecycle_error_from_stop_evidence(tmp_path, monkeypatch):
    evidence = LocalEvidenceStore(tmp_path / "evidence")
    scheduler = RuntimePeriodicScheduler(evidence)
    original_event = evidence.event
    original = LifecycleError("stop reporting failed")
    task = scheduler.create("reporting", interval_s=60, callback=lambda: None)

    def event(name, **fields):
        if name == "periodic_stopped":
            raise original
        original_event(name, **fields)

    monkeypatch.setattr(evidence, "event", event)
    try:
        task.acquire(startup_timeout_s=2)
        with pytest.raises(LifecycleError) as failure:
            scheduler.close()
        assert failure.value is original
        assert task.leases == 0
        scheduler.close()
    finally:
        evidence.close()


@pytest.mark.parametrize(
    "original", [None, ValueError("operation"), KeyboardInterrupt("operation")]
)
def test_lease_stop_failure_preserves_original_exception(tmp_path, monkeypatch, original):
    evidence = LocalEvidenceStore(tmp_path)
    scheduler = RuntimePeriodicScheduler(evidence)
    task = scheduler.create("lease", interval_s=60, callback=lambda: None)
    event = evidence.event
    reporting = OSError("stop reporting failed")

    def fail_event(name, **fields):
        if name == "periodic_stopped":
            raise reporting
        event(name, **fields)

    expected = original if original is not None else reporting
    try:
        with monkeypatch.context() as patch:
            patch.setattr(evidence, "event", fail_event)
            with pytest.raises(type(expected)) as failure:
                with task.lease(startup_timeout_s=2):
                    if original is not None:
                        raise original
            assert failure.value is expected
            if original is not None:
                assert repr(reporting) in " ".join(original.__notes__)
        assert task.leases == 0
        with task.lease(startup_timeout_s=2):
            pass
    finally:
        scheduler.close()
        evidence.close()


@pytest.mark.parametrize("phase", ["creation_evidence", "thread_start"])
def test_periodic_startup_failure_leaves_no_stale_task_or_worker(tmp_path, monkeypatch, phase):
    evidence = LocalEvidenceStore(tmp_path / "evidence")
    scheduler = RuntimePeriodicScheduler(evidence)
    original_event = evidence.event
    original = KeyboardInterrupt("periodic startup interrupted")

    def event(name, **fields):
        if name == "periodic_created":
            raise original
        original_event(name, **fields)

    def start(thread):
        raise original

    try:
        with monkeypatch.context() as patch:
            if phase == "creation_evidence":
                patch.setattr(evidence, "event", event)
                with pytest.raises(KeyboardInterrupt) as failure:
                    scheduler.create("retry", interval_s=60, callback=lambda: None)
                assert scheduler.names() == ()
            else:
                task = scheduler.create("retry", interval_s=60, callback=lambda: None)
                patch.setattr(threading.Thread, "start", start)
                with pytest.raises(KeyboardInterrupt) as failure:
                    task.acquire()
                assert task.leases == 0
            assert failure.value is original
        if phase == "creation_evidence":
            task = scheduler.create("retry", interval_s=60, callback=lambda: None)
        with task.lease(startup_timeout_s=2):
            pass
    finally:
        scheduler.close()
        evidence.close()
