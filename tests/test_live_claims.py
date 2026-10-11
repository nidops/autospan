"""Claim validation inside plans and across separate runtime acquisitions."""

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from autospan.api import (
    ClaimMode,
    ComponentSpec,
    GateError,
    LifecycleError,
    PlanError,
    ResourceClaim,
    RuntimePlan,
)
from autospan._core.evidence_store import LocalEvidenceStore
from autospan._core.runtime import RuntimeSession


class Factory:
    def __init__(self, calls: list[str], fail: bool = False) -> None:
        self.calls = calls
        self.fail = fail

    @contextmanager
    def open(self, context, dependencies, config):
        self.calls.append("open")
        if self.fail:
            raise RuntimeError("open failed")
        try:
            yield object()
        finally:
            self.calls.append("close")


def make_session(
    tmp_path, label, calls, mode=ClaimMode.EXCLUSIVE, fail=False, *, factory=None, claims=None
):
    evidence = LocalEvidenceStore(tmp_path / label)
    session = RuntimeSession(
        plan=RuntimePlan(
            components=(
                ComponentSpec(
                    "bench",
                    "bench",
                    claims=(ResourceClaim("serial:physical-1", mode),)
                    if claims is None
                    else claims,
                ),
            )
        ),
        factories={"bench": Factory(calls, fail) if factory is None else factory},
        evidence=evidence,
    )
    return session, evidence


def test_exclusive_resource_cannot_open_in_parallel_sessions(tmp_path):
    calls = []
    first, first_evidence = make_session(tmp_path, "first", calls)
    second, second_evidence = make_session(tmp_path, "second", calls)
    try:
        with first:
            with pytest.raises(LifecycleError, match="already claimed"):
                with second:
                    pass
            assert calls == ["open"]
        with second:
            assert calls == ["open", "close", "open"]
    finally:
        first_evidence.close()
        second_evidence.close()
    assert calls == ["open", "close", "open", "close"]


def test_shared_claims_can_coexist_but_exclusive_is_blocked(tmp_path):
    calls = []
    a, ev_a = make_session(tmp_path, "a", calls, ClaimMode.SHARED)
    b, ev_b = make_session(tmp_path, "b", calls, ClaimMode.SHARED)
    exclusive, ev_c = make_session(tmp_path, "c", calls)
    try:
        with a, b:
            with pytest.raises(LifecycleError, match="already claimed"):
                with exclusive:
                    pass
        with exclusive:
            pass
    finally:
        for evidence in (ev_a, ev_b, ev_c):
            evidence.close()


@pytest.mark.parametrize(
    ("first_mode", "second_mode"),
    [
        (ClaimMode.EXCLUSIVE, ClaimMode.EXCLUSIVE),
        (ClaimMode.EXCLUSIVE, ClaimMode.SHARED),
        (ClaimMode.SHARED, ClaimMode.EXCLUSIVE),
    ],
)
def test_direct_conflicting_plan_is_rejected_before_acquisition(tmp_path, first_mode, second_mode):
    calls = []
    evidence = LocalEvidenceStore(tmp_path / "conflicting")
    session = RuntimeSession(
        plan=RuntimePlan(
            (
                ComponentSpec(
                    "first",
                    "bench",
                    claims=(
                        ResourceClaim("other:1"),
                        ResourceClaim("serial:physical-1", first_mode),
                    ),
                ),
                ComponentSpec(
                    "second",
                    "bench",
                    claims=(ResourceClaim("serial:physical-1", second_mode),),
                ),
            )
        ),
        factories={"bench": Factory(calls)},
        evidence=evidence,
    )
    other, other_evidence = make_session(
        tmp_path,
        "other",
        calls,
        claims=(ResourceClaim("other:1"), ResourceClaim("serial:physical-1")),
    )
    try:
        for _ in range(2):
            with pytest.raises(
                PlanError,
                match="Resource claim conflict for 'serial:physical-1': 'second' conflicts with first",
            ):
                with session:
                    pytest.fail("conflicting plan opened")
        assert calls == []
        with other:
            assert calls == ["open"]
        assert calls == ["open", "close"]
    finally:
        evidence.close()
        other_evidence.close()


def test_direct_shared_plan_opens_all_holders_and_releases_claims(tmp_path):
    calls = []
    evidence = LocalEvidenceStore(tmp_path / "shared")
    session = RuntimeSession(
        plan=RuntimePlan(
            tuple(
                ComponentSpec(
                    name,
                    "bench",
                    claims=(ResourceClaim("serial:physical-1", ClaimMode.SHARED),),
                )
                for name in ("first", "second")
            )
        ),
        factories={"bench": Factory(calls)},
        evidence=evidence,
    )
    exclusive, exclusive_evidence = make_session(tmp_path, "exclusive", calls)
    try:
        with session as runtime:
            assert runtime.names() == ("first", "second")
            assert calls == ["open", "open"]
            with pytest.raises(LifecycleError, match="already claimed"):
                with exclusive:
                    pytest.fail("exclusive holder opened while shared holders were active")
            assert calls == ["open", "open"]
        assert calls == ["open", "open", "close", "close"]
        with exclusive:
            pass
        assert calls == ["open", "open", "close", "close", "open", "close"]
    finally:
        evidence.close()
        exclusive_evidence.close()


def test_startup_failure_releases_claim(tmp_path):
    calls = []
    failed, failed_evidence = make_session(tmp_path, "failed", calls, fail=True)
    other, other_evidence = make_session(tmp_path, "other", calls)
    try:
        with pytest.raises(RuntimeError, match="open failed"):
            with failed:
                pass
        with other:
            pass
    finally:
        failed_evidence.close()
        other_evidence.close()


def test_gate_failure_does_not_reserve_claims(tmp_path):
    calls = []
    evidence = LocalEvidenceStore(tmp_path / "gated")
    gated = RuntimeSession(
        plan=RuntimePlan(
            (
                ComponentSpec(
                    "bench",
                    "bench",
                    claims=(ResourceClaim("serial:physical-1"),),
                    gates=("hardware",),
                ),
            )
        ),
        factories={"bench": Factory(calls)},
        evidence=evidence,
        enabled_gates=(),
    )
    other, other_evidence = make_session(tmp_path, "other", calls)
    try:
        for _ in range(2):
            with pytest.raises(GateError, match="require --hardware"):
                with gated:
                    pass
        assert calls == []
        with other:
            assert calls == ["open"]
    finally:
        evidence.close()
        other_evidence.close()


def test_partial_handover_release_also_reacquires_failing_holder(tmp_path):
    class PartialRelease:
        def __init__(self):
            self.released = False
            self.reacquired = False

        def release(self):
            self.released = True
            raise OSError("USB handover failed after releasing resource")

        def reacquire(self):
            self.reacquired = True
            self.released = False

    class PartialFactory:
        @contextmanager
        def open(self, context, dependencies, config):
            yield holder

    holder = PartialRelease()
    evidence = LocalEvidenceStore(tmp_path / "handover")
    session = RuntimeSession(
        plan=RuntimePlan(
            components=(ComponentSpec("device", "partial", claims=(ResourceClaim("usb:1"),)),)
        ),
        factories={"partial": PartialFactory()},
        evidence=evidence,
    )
    try:
        with session as runtime:
            with pytest.raises(OSError, match="after releasing"):
                with runtime.handover("usb:1"):
                    pytest.fail("handover body ran after failed release")
            assert holder.reacquired
            assert not holder.released
    finally:
        evidence.close(outcome="failed")


def test_runtime_session_cannot_enter_twice(tmp_path):
    calls = []
    session, evidence = make_session(tmp_path, "single", calls)
    try:
        with session:
            with pytest.raises(LifecycleError, match="already active"):
                session.__enter__()
            assert calls == ["open"]
    finally:
        evidence.close()


def test_same_session_cannot_enter_concurrently_before_claim_reservation(tmp_path, monkeypatch):
    evidence = LocalEvidenceStore(tmp_path / "empty")
    session = RuntimeSession(plan=RuntimePlan(()), factories={}, evidence=evidence)
    waiting, resume = threading.Event(), threading.Event()
    main_thread = threading.current_thread()

    def missing_gates():
        if threading.current_thread() is not main_thread:
            waiting.set()
            assert resume.wait(5)
        return {}

    monkeypatch.setattr(session, "missing_gates", missing_gates)

    def enter():
        with session:
            pass

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(enter)
            try:
                assert waiting.wait(5)
                with pytest.raises(LifecycleError, match="already active"):
                    with session:
                        pass
            finally:
                resume.set()
                future.result(timeout=5)
    finally:
        evidence.close()


def test_handover_refuses_other_live_shared_holders_before_releasing(tmp_path):
    log = []

    class Holder:
        def release(self):
            log.append("release")

        def reacquire(self):
            log.append("reacquire")

    class HolderFactory:
        @contextmanager
        def open(self, context, dependencies, config):
            yield Holder()

    first, ev_a = make_session(tmp_path, "a", [], ClaimMode.SHARED, factory=HolderFactory())
    second, ev_b = make_session(tmp_path, "b", [], ClaimMode.SHARED, factory=HolderFactory())
    try:
        with first as runtime, second:
            with pytest.raises(LifecycleError, match="another live runtime"):
                with runtime.handover("serial:physical-1"):
                    pytest.fail("tool ran while another session held the resource")
            assert log == []
    finally:
        ev_a.close()
        ev_b.close()


def test_handover_blocks_new_shared_holders_until_reacquisition(tmp_path):
    calls = []

    class Holder:
        def release(self):
            calls.append("release")

        def reacquire(self):
            with pytest.raises(LifecycleError, match="handed over"):
                with second:
                    pytest.fail("shared driver opened before reacquisition finished")
            calls.append("reacquire")

    class HolderFactory:
        @contextmanager
        def open(self, context, dependencies, config):
            yield Holder()

    first, ev_a = make_session(tmp_path, "a", [], ClaimMode.SHARED, factory=HolderFactory())
    second, ev_b = make_session(tmp_path, "b", calls, ClaimMode.SHARED)
    try:
        with first as runtime:
            with runtime.handover("serial:physical-1"):
                with pytest.raises(LifecycleError, match="handed over"):
                    with second:
                        pytest.fail("shared driver opened while external tool owned the resource")
                assert calls == ["release"]
            with second:
                assert calls == ["release", "reacquire", "open"]
    finally:
        ev_a.close()
        ev_b.close()


def test_conflicting_plan_does_not_reserve_its_other_claims(tmp_path):
    calls = []
    first, ev_a = make_session(tmp_path, "a", calls)
    both, ev_b = make_session(
        tmp_path,
        "both",
        calls,
        claims=(ResourceClaim("other:1"), ResourceClaim("serial:physical-1")),
    )
    independent, ev_c = make_session(
        tmp_path,
        "independent",
        calls,
        claims=(ResourceClaim("other:1"),),
    )
    try:
        with first:
            with pytest.raises(LifecycleError, match="already claimed"):
                with both:
                    pass
            with independent:
                assert calls == ["open", "open"]
    finally:
        for evidence in (ev_a, ev_b, ev_c):
            evidence.close()


def test_interrupted_reservation_rolls_back_partially_recorded_claims(tmp_path, monkeypatch):
    from autospan._core import active_claims

    original = KeyboardInterrupt("reservation interrupted")

    class InterruptedClaims(dict):
        interrupted = False

        def setdefault(self, key, default):
            if key == "second:1" and not self.interrupted:
                self.interrupted = True
                raise original
            return super().setdefault(key, default)

    monkeypatch.setattr(active_claims, "_ACTIVE", InterruptedClaims())
    calls = []
    claims = (ResourceClaim("first:1"), ResourceClaim("second:1"))
    session, evidence = make_session(tmp_path, "interrupted", calls, claims=claims)
    other, other_evidence = make_session(tmp_path, "other", calls, claims=claims)
    try:
        with pytest.raises(KeyboardInterrupt) as failure:
            with session:
                pass
        assert failure.value is original
        assert calls == []
        with other:
            pass
        with session:
            pass
    finally:
        evidence.close()
        other_evidence.close()


def test_interrupted_handover_reservation_does_not_block_later_sessions(tmp_path, monkeypatch):
    from autospan._core import active_claims

    original = KeyboardInterrupt("handover interrupted")

    class InterruptedHandovers(dict):
        def __setitem__(self, key, value):
            super().__setitem__(key, value)
            raise original

    monkeypatch.setattr(active_claims, "_HANDOVERS", InterruptedHandovers())
    first, ev_a = make_session(tmp_path, "a", [], ClaimMode.SHARED)
    second, ev_b = make_session(tmp_path, "b", [], ClaimMode.SHARED)
    try:
        with first as runtime:
            with pytest.raises(KeyboardInterrupt) as failure:
                with runtime.handover("serial:physical-1"):
                    pytest.fail("tool ran after handover reservation failed")
            assert failure.value is original
            with second:
                pass
    finally:
        ev_a.close()
        ev_b.close()


def test_simultaneous_exclusive_sessions_open_exactly_one_driver(tmp_path):
    calls = []
    first, ev_a = make_session(tmp_path, "a", calls)
    second, ev_b = make_session(tmp_path, "b", calls)
    start, attempted = threading.Barrier(2), threading.Barrier(2)

    def enter(session):
        start.wait(timeout=5)
        try:
            with session:
                attempted.wait(timeout=5)
                return "opened"
        except LifecycleError as error:
            assert "already claimed" in str(error)
            attempted.wait(timeout=5)
            return "blocked"

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(enter, (first, second)))
        assert sorted(results) == ["blocked", "opened"]
        assert calls == ["open", "close"]
        with second:
            pass
    finally:
        ev_a.close()
        ev_b.close()


@pytest.mark.parametrize("phase", ["scheduler", "evidence", "factory"])
def test_failed_startup_releases_claims_and_allows_retry(tmp_path, monkeypatch, phase):
    import autospan._core.runtime as runtime_module

    calls = []
    original = KeyboardInterrupt("startup interrupted")

    class BrokenFactory:
        @contextmanager
        def open(self, context, dependencies, config):
            raise original
            yield  # pragma: no cover

    session, evidence = make_session(tmp_path, "failed", calls)
    other, other_evidence = make_session(tmp_path, "other", calls)
    try:
        with monkeypatch.context() as patch:
            if phase == "scheduler":

                def broken_scheduler(evidence):
                    raise original

                patch.setattr(runtime_module, "RuntimePeriodicScheduler", broken_scheduler)
            elif phase == "evidence":
                original_event = evidence.event

                def event(name, **attributes):
                    if name == "runtime_starting":
                        raise original
                    original_event(name, **attributes)

                patch.setattr(evidence, "event", event)
            else:
                patch.setattr(session, "_factories", {"bench": BrokenFactory()})
            with pytest.raises(KeyboardInterrupt) as failure:
                with session:
                    pass
            assert failure.value is original
        with other:
            pass
        with session:
            pass
    finally:
        evidence.close()
        other_evidence.close()


def test_claims_stay_reserved_through_failed_cleanup_and_are_then_released(tmp_path):
    calls = []
    other, other_evidence = make_session(tmp_path, "other", calls)

    class BrokenCleanup:
        @contextmanager
        def open(self, context, dependencies, config):
            try:
                yield object()
            finally:
                with pytest.raises(LifecycleError, match="already claimed"):
                    with other:
                        pytest.fail("driver opened while another was closing")
                raise OSError("cleanup failed")

    session, evidence = make_session(tmp_path, "failed", calls, factory=BrokenCleanup())
    try:
        with pytest.raises(LifecycleError, match="cleanup failed"):
            with session:
                pass
        with other:
            assert calls == ["open"]
    finally:
        evidence.close()
        other_evidence.close()


def test_failed_handover_releases_temporary_reservation(tmp_path):
    calls = []

    class Holder:
        def release(self):
            raise OSError("partial release")

        def reacquire(self):
            calls.append("reacquire")

    class HolderFactory:
        @contextmanager
        def open(self, context, dependencies, config):
            yield Holder()

    first, ev_a = make_session(tmp_path, "a", [], ClaimMode.SHARED, factory=HolderFactory())
    second, ev_b = make_session(tmp_path, "b", calls, ClaimMode.SHARED)
    try:
        with first as runtime:
            with pytest.raises(OSError, match="partial release"):
                with runtime.handover("serial:physical-1"):
                    pass
            with second:
                assert calls == ["reacquire", "open"]
    finally:
        ev_a.close()
        ev_b.close()
