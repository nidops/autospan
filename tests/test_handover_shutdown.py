"""Deterministic ownership checks across handover recovery and runtime shutdown."""

from contextlib import contextmanager
from threading import Barrier, Event, Thread

import pytest

from autospan.api import ClaimMode, ComponentSpec, LifecycleError, ResourceClaim, RuntimePlan
from autospan._core.evidence_store import LocalEvidenceStore
from autospan._core.runtime import RuntimeSession


def _start(action):
    errors = []

    def run():
        try:
            action()
        except BaseException as error:
            errors.append(error)

    thread = Thread(target=run, daemon=True)
    thread.start()
    return thread, errors


def _join(thread):
    thread.join(5)
    assert not thread.is_alive(), "lifecycle operation deadlocked"


@pytest.fixture
def session_factory(tmp_path):
    stores = []

    def make(factory, *, names=("device",), mode=ClaimMode.SHARED):
        evidence = LocalEvidenceStore(tmp_path / str(len(stores)))
        stores.append(evidence)
        return RuntimeSession(
            plan=RuntimePlan(
                tuple(
                    ComponentSpec(
                        name,
                        "device",
                        dependencies=(names[index - 1],) if index else (),
                        claims=(ResourceClaim("handover:shutdown", mode),),
                        config={"name": name},
                    )
                    for index, name in enumerate(names)
                )
            ),
            factories={"device": factory},
            evidence=evidence,
        )

    yield make
    for store in stores:
        store.close()


class _PlainFactory:
    @contextmanager
    def open(self, context, dependencies, config):
        yield object()


@pytest.mark.parametrize(
    "body_error,recovery_error,cleanup_error",
    [
        (None, None, None),
        (ValueError("tool failed"), None, None),
        (KeyboardInterrupt("tool interrupted"), OSError("recovery failed"), None),
        (None, OSError("recovery failed"), None),
        (None, KeyboardInterrupt("recovery interrupted"), OSError("cleanup failed")),
    ],
)
def test_shutdown_waits_through_handover_recovery_and_diagnostics(
    session_factory, monkeypatch, body_error, recovery_error, cleanup_error
):
    body, finish_body = Event(), Event()
    recovering, finish_recovery = Event(), Event()
    reporting, finish_reporting = Event(), Event()
    stopping, cleaning, finish_cleanup = Event(), Event(), Event()
    log = []

    class Holder:
        def __init__(self, name):
            self.name = name

        def release(self):
            log.append(f"release:{self.name}")

        def reacquire(self):
            if self.name == "a":
                recovering.set()
                assert finish_recovery.wait(5)
                if recovery_error is not None:
                    raise recovery_error
            log.append(f"reacquire:{self.name}")

    class Factory:
        @contextmanager
        def open(self, context, dependencies, config):
            name = config["name"]
            log.append(f"open:{name}")
            try:
                yield Holder(name)
            finally:
                cleaning.set()
                assert finish_cleanup.wait(5)
                log.append(f"close:{name}")
                if cleanup_error is not None:
                    raise cleanup_error

    session = session_factory(Factory(), names=("a", "b"))
    shared = session_factory(_PlainFactory())
    exclusive = session_factory(_PlainFactory(), mode=ClaimMode.EXCLUSIVE)
    original_event = session.evidence.event

    def event(name, **fields):
        original_event(name, **fields)
        if name == "runtime_stopping":
            stopping.set()
        elif name == "resource_reacquire_failed":
            reporting.set()
            assert finish_reporting.wait(5)

    monkeypatch.setattr(session.evidence, "event", event)
    runtime = session.__enter__()

    def handover():
        with runtime.handover("handover:shutdown"):
            body.set()
            assert finish_body.wait(5)
            if body_error is not None:
                raise body_error

    worker, handover_errors = _start(handover)
    closer = None
    try:
        assert body.wait(5)
        closer, shutdown_errors = _start(lambda: session.__exit__(None, None, None))
        assert stopping.wait(5)
        with pytest.raises(LifecycleError, match="stopping"):
            with runtime.handover("handover:shutdown"):
                pytest.fail("handover admitted during shutdown")
        with pytest.raises(LifecycleError, match="already active"):
            session.__enter__()
        with pytest.raises(LifecycleError, match="not active"):
            session.__exit__(None, None, None)
        assert not cleaning.is_set()
        with pytest.raises(LifecycleError, match="handed over"):
            shared.__enter__()
        finish_body.set()
        assert recovering.wait(5)
        assert not cleaning.is_set()
        with pytest.raises(LifecycleError, match="handed over"):
            shared.__enter__()
        finish_recovery.set()
        if recovery_error is not None:
            assert reporting.wait(5)
            assert not cleaning.is_set()
            with pytest.raises(LifecycleError, match="handed over"):
                shared.__enter__()
        finish_reporting.set()
        assert cleaning.wait(5)
        # Handover recovery has ended, but the final component reservation remains.
        with pytest.raises(LifecycleError, match="already claimed"):
            exclusive.__enter__()
        with pytest.raises(LifecycleError, match="already active"):
            session.__enter__()
    finally:
        finish_body.set()
        finish_recovery.set()
        finish_reporting.set()
        finish_cleanup.set()
        _join(worker)
        if closer is not None:
            _join(closer)

    if body_error is not None:
        assert handover_errors == [body_error]
        if recovery_error is not None:
            assert repr(recovery_error) in " ".join(body_error.__notes__)
    elif recovery_error is not None:
        assert len(handover_errors) == 1
        assert isinstance(handover_errors[0], LifecycleError)
        assert handover_errors[0].__cause__ is recovery_error
    else:
        assert handover_errors == []
    if cleanup_error is not None:
        assert len(shutdown_errors) == 1
        assert isinstance(shutdown_errors[0], LifecycleError)
        assert shutdown_errors[0].__cause__ is cleanup_error
    elif recovery_error is not None:
        assert len(shutdown_errors) == 1
        assert isinstance(shutdown_errors[0], LifecycleError)
        assert "not recovered" in str(shutdown_errors[0])
    else:
        assert shutdown_errors == []
    assert log[:4] == ["open:a", "open:b", "release:b", "release:a"]
    assert log[-3:] == ["reacquire:b", "close:b", "close:a"]
    with exclusive:
        pass
    if cleanup_error is None:
        if recovery_error is None:
            with session as reused:
                with reused.handover("handover:shutdown"):
                    pass
        else:
            with pytest.raises(LifecycleError, match="not recovered"):
                with session as reused:
                    with pytest.raises(LifecycleError) as failure:
                        with reused.handover("handover:shutdown"):
                            pass
                    assert failure.value.__cause__ is recovery_error
    with exclusive:
        pass


def test_competing_handovers_admit_exactly_one_body(session_factory):
    start, attempted = Barrier(3), Barrier(3)
    log = []

    class Holder:
        def release(self):
            log.append("release")

        def reacquire(self):
            log.append("reacquire")

    class Factory:
        @contextmanager
        def open(self, context, dependencies, config):
            yield Holder()

    session = session_factory(Factory())
    results = []

    def attempt():
        start.wait(5)
        try:
            with session.handover("handover:shutdown"):
                results.append("admitted")
                attempted.wait(5)
        except LifecycleError as error:
            assert "already handed over" in str(error)
            results.append("rejected")
            attempted.wait(5)

    with session:
        workers = [_start(attempt) for _ in range(3)]
        for worker, errors in workers:
            _join(worker)
            assert errors == []
        assert sorted(results) == ["admitted", "rejected", "rejected"]
        assert log == ["release", "reacquire"]
        with session.handover("handover:shutdown"):
            pass
    assert log == ["release", "reacquire", "release", "reacquire"]
    with session_factory(_PlainFactory(), mode=ClaimMode.EXCLUSIVE):
        pass


@pytest.mark.parametrize("admitted", [False, True])
def test_periodic_handover_can_finish_while_runtime_stops(session_factory, monkeypatch, admitted):
    ready, stopping, finish = Event(), Event(), Event()
    log = []

    class Holder:
        def release(self):
            log.append("release")

        def reacquire(self):
            log.append("reacquire")

    class Factory:
        @contextmanager
        def open(self, context, dependencies, config):
            try:
                yield Holder()
            finally:
                log.append("close")

    session = session_factory(Factory())
    original_event = session.evidence.event

    def event(name, **fields):
        original_event(name, **fields)
        if name == "runtime_stopping":
            stopping.set()

    monkeypatch.setattr(session.evidence, "event", event)
    runtime = session.__enter__()

    def callback():
        if admitted:
            with runtime.handover("handover:shutdown"):
                ready.set()
                assert stopping.wait(5)
                with pytest.raises(LifecycleError, match="stopping"):
                    with runtime.handover("handover:shutdown"):
                        pytest.fail("nested handover admitted after shutdown began")
                assert finish.wait(5)
        else:
            ready.set()
            assert stopping.wait(5)
            with pytest.raises(LifecycleError, match="stopping"):
                with runtime.handover("handover:shutdown"):
                    pytest.fail("periodic handover admitted after shutdown began")
            assert finish.wait(5)
        log.append("worker-returned")

    task = runtime.periodic.create("handover", interval_s=60, callback=callback)
    closer = None
    try:
        task.acquire()
        assert ready.wait(5)
        closer, errors = _start(lambda: session.__exit__(None, None, None))
        assert stopping.wait(5)
        assert "close" not in log
    finally:
        finish.set()
        if closer is not None:
            _join(closer)
    assert errors == []
    assert log == (["release", "reacquire"] if admitted else []) + ["worker-returned", "close"]
    with session:
        pass


def test_interrupt_during_handover_wait_defers_cleanup(session_factory, monkeypatch):
    entered, finish, interrupted = Event(), Event(), Event()
    log = []
    interruption = KeyboardInterrupt("handover wait interrupted")

    class Holder:
        def release(self):
            pass

        def reacquire(self):
            log.append("reacquire")

    class Factory:
        @contextmanager
        def open(self, context, dependencies, config):
            try:
                yield Holder()
            finally:
                log.append("close")

    session = session_factory(Factory())
    original_wait = session._lifecycle.wait

    def wait(timeout=None):
        if not interrupted.is_set():
            interrupted.set()
            raise interruption
        return original_wait(timeout)

    monkeypatch.setattr(session._lifecycle, "wait", wait)
    runtime = session.__enter__()

    def handover():
        with runtime.handover("handover:shutdown"):
            entered.set()
            assert finish.wait(5)

    worker, worker_errors = _start(handover)
    closer = None
    try:
        assert entered.wait(5)
        closer, errors = _start(lambda: session.__exit__(None, None, None))
        assert interrupted.wait(5)
        assert log == []
    finally:
        finish.set()
        _join(worker)
        if closer is not None:
            _join(closer)
    assert worker_errors == []
    assert len(errors) == 1
    assert errors[0].__cause__ is interruption
    assert log == ["reacquire", "close"]
    with session:
        pass
