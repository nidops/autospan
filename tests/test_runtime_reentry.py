"""Reentrant shutdown and startup rollback with an admitted resource handover."""

from contextlib import contextmanager
from threading import Event, Thread

import pytest

from autospan.api import ComponentSpec, LifecycleError, ResourceClaim, RuntimePlan
from autospan._core.evidence_store import LocalEvidenceStore
from autospan._core.runtime import RuntimeSession


def _run_bounded(action):
    errors = []

    def run():
        try:
            action()
        except BaseException as error:
            errors.append(error)

    thread = Thread(target=run, daemon=True)
    thread.start()
    thread.join(5)
    assert not thread.is_alive(), "runtime operation deadlocked"
    if errors:
        raise errors[0]


def test_teardown_inside_handover_is_rejected_without_deactivating_runtime(tmp_path):
    log = []

    class Holder:
        def release(self):
            log.append("release")

        def reacquire(self):
            log.append("reacquire")

    holder = Holder()

    class Factory:
        @contextmanager
        def open(self, context, dependencies, config):
            log.append("open")
            try:
                yield holder
            finally:
                log.append("close")

    evidence = LocalEvidenceStore(tmp_path / "handover")
    session = RuntimeSession(
        plan=RuntimePlan(
            (ComponentSpec("device", "device", claims=(ResourceClaim("reentry:handover"),)),)
        ),
        factories={"device": Factory()},
        evidence=evidence,
    )

    def exercise():
        try:
            with session as runtime:
                with runtime.handover("reentry:handover"):
                    with pytest.raises(LifecycleError, match="inside its handover"):
                        session.__exit__(None, None, None)
                    assert runtime.get("device") is holder
                    assert log == ["open", "release"]
                with runtime.handover("reentry:handover"):
                    assert runtime.get("device") is holder
            assert log == ["open", "release", "reacquire", "release", "reacquire", "close"]
            with session:
                pass
            assert log[-2:] == ["open", "close"]
        finally:
            evidence.close()

    _run_bounded(exercise)


@pytest.mark.parametrize("phase", ["evidence", "component"])
def test_reentrant_teardown_is_rejected_from_cleanup_callbacks(tmp_path, monkeypatch, phase):
    log = []
    attempted = False

    def attempt_teardown():
        nonlocal attempted
        if attempted:
            return
        attempted = True
        with pytest.raises(LifecycleError, match="not active|stopping"):
            session.__exit__(None, None, None)
        log.append("rejected")

    class Factory:
        @contextmanager
        def open(self, context, dependencies, config):
            log.append("open")
            try:
                yield object()
            finally:
                if phase == "component":
                    attempt_teardown()
                log.append("close")

    evidence = LocalEvidenceStore(tmp_path / phase)
    original_event = evidence.event

    def event(name, **attributes):
        if name == "runtime_stopping" and phase == "evidence":
            attempt_teardown()
        original_event(name, **attributes)

    monkeypatch.setattr(evidence, "event", event)
    session = RuntimeSession(
        plan=RuntimePlan(
            (ComponentSpec("device", "device", claims=(ResourceClaim(f"reentry:{phase}"),)),)
        ),
        factories={"device": Factory()},
        evidence=evidence,
    )

    def exercise():
        try:
            with session:
                pass
            assert log == ["open", "rejected", "close"]
            with session:
                pass
            assert log[-2:] == ["open", "close"]
        finally:
            evidence.close()

    _run_bounded(exercise)


def test_startup_rollback_waits_for_handover_recovery_and_allows_retry(tmp_path, monkeypatch):
    body_entered, finish_body = Event(), Event()
    recovering, finish_recovery = Event(), Event()
    rollback_started, component_closed = Event(), Event()
    startup_errors, handover_errors = [], []
    original = KeyboardInterrupt("startup interrupted")
    cleanup_error = OSError("first component cleanup failed")
    log = []
    handover_thread = None

    class Holder:
        def release(self):
            log.append("release")

        def reacquire(self):
            recovering.set()
            assert finish_recovery.wait(5), "recovery was not released"
            log.append("reacquire")

    class HolderFactory:
        first_cleanup = True

        @contextmanager
        def open(self, context, dependencies, config):
            log.append("open")
            try:
                yield Holder()
            finally:
                log.append("close")
                component_closed.set()
                if self.first_cleanup:
                    self.first_cleanup = False
                    raise cleanup_error

    class FailOnceFactory:
        failed = False

        @contextmanager
        def open(self, context, dependencies, config):
            nonlocal handover_thread
            if not self.failed:
                self.failed = True

                def handover():
                    try:
                        with context.handover("reentry:startup"):
                            body_entered.set()
                            assert finish_body.wait(5), "handover body was not released"
                    except BaseException as error:
                        handover_errors.append(error)

                handover_thread = Thread(target=handover, daemon=True)
                handover_thread.start()
                assert body_entered.wait(5), "startup handover never entered"
                raise original
            yield object()

    evidence = LocalEvidenceStore(tmp_path / "startup")
    original_event = evidence.event

    def event(name, **attributes):
        original_event(name, **attributes)
        if name == "runtime_start_failed":
            rollback_started.set()

    monkeypatch.setattr(evidence, "event", event)
    session = RuntimeSession(
        plan=RuntimePlan(
            (
                ComponentSpec("device", "device", claims=(ResourceClaim("reentry:startup"),)),
                ComponentSpec("later", "later", dependencies=("device",)),
            )
        ),
        factories={"device": HolderFactory(), "later": FailOnceFactory()},
        evidence=evidence,
    )

    class OtherFactory:
        @contextmanager
        def open(self, context, dependencies, config):
            yield object()

    other_evidence = LocalEvidenceStore(tmp_path / "other")
    other = RuntimeSession(
        plan=RuntimePlan(
            (ComponentSpec("other", "other", claims=(ResourceClaim("reentry:startup"),)),)
        ),
        factories={"other": OtherFactory()},
        evidence=other_evidence,
    )

    def enter():
        try:
            with session:
                pytest.fail("first startup unexpectedly succeeded")
        except BaseException as error:
            startup_errors.append(error)

    startup_thread = Thread(target=enter, daemon=True)
    startup_thread.start()
    try:
        assert rollback_started.wait(5), "startup rollback never began"
        assert startup_thread.is_alive()
        assert not component_closed.is_set()
        with pytest.raises(LifecycleError, match="stopping"):
            with session.handover("reentry:startup"):
                pytest.fail("new handover entered during rollback")
        with pytest.raises(LifecycleError, match="handed over|already claimed"):
            with other:
                pytest.fail("resource was released during rollback")
        finish_body.set()
        assert recovering.wait(5), "handover recovery never began"
        assert startup_thread.is_alive()
        assert not component_closed.is_set()
        with pytest.raises(LifecycleError, match="handed over|already claimed"):
            with other:
                pytest.fail("resource was released before reacquisition finished")
    finally:
        finish_body.set()
        finish_recovery.set()
        startup_thread.join(5)
        if handover_thread is not None:
            handover_thread.join(5)

    assert not startup_thread.is_alive(), "startup rollback deadlocked"
    assert handover_thread is not None and not handover_thread.is_alive()
    assert startup_errors == [original]
    assert handover_errors == []
    assert "first component cleanup failed" in " ".join(original.__notes__)
    assert log == ["open", "release", "reacquire", "close"]
    try:
        with other:
            pass
        with session as runtime:
            assert runtime.names() == ("device", "later")
            with runtime.handover("reentry:startup"):
                pass
        with other:
            pass
    finally:
        evidence.close()
        other_evidence.close()
