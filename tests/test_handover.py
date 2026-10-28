"""Generic hand-over of a claimed resource to an external tool."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from autospan.api import ClaimMode, ComponentSpec, LifecycleError, ResourceClaim, RuntimePlan
from autospan._core.evidence_store import LocalEvidenceStore
from autospan._core.runtime import RuntimeSession


class _Holder:
    def __init__(self, name: str, log: list[str], fail_release: bool = False) -> None:
        self.name, self.log, self.fail_release = name, log, fail_release

    def release(self) -> None:
        if self.fail_release:
            raise RuntimeError("busy")
        self.log.append(f"release {self.name}")

    def reacquire(self) -> None:
        self.log.append(f"reacquire {self.name}")


class _Plain:
    pass


class _Factory:
    def __init__(self, make) -> None:
        self._make = make

    @contextmanager
    def open(self, context, dependencies, config):
        yield self._make(config["name"])


def _session(tmp_path: Path, make, names=("a", "b"), shared=ClaimMode.SHARED) -> RuntimeSession:
    specs = tuple(
        ComponentSpec(
            n,
            "t",
            dependencies=(names[i - 1],) if i else (),
            claims=(ResourceClaim("chan:1", shared),),
            config={"name": n},
        )
        for i, n in enumerate(names)
    )
    return RuntimeSession(
        plan=RuntimePlan(components=specs),
        factories={"t": _Factory(make)},
        evidence=LocalEvidenceStore(tmp_path / "e"),
    )


def test_handover_releases_dependents_first_and_reacquires_in_reverse(tmp_path: Path) -> None:
    log: list[str] = []
    with _session(tmp_path, lambda n: _Holder(n, log)) as runtime:
        with runtime.handover("chan:1"):
            log.append("tool")
    assert log == ["release b", "release a", "tool", "reacquire a", "reacquire b"]


def test_reacquire_happens_even_when_the_tool_fails(tmp_path: Path) -> None:
    log: list[str] = []
    with _session(tmp_path, lambda n: _Holder(n, log)) as runtime:
        with pytest.raises(ValueError):
            with runtime.handover("chan:1"):
                raise ValueError("tool crashed")
    assert log[-2:] == ["reacquire a", "reacquire b"]


def test_failed_release_rolls_back_those_already_released(tmp_path: Path) -> None:
    log: list[str] = []
    made = {"a": lambda: _Holder("a", log, fail_release=True), "b": lambda: _Holder("b", log)}
    with _session(tmp_path, lambda n: made[n]()) as runtime:
        with pytest.raises(RuntimeError, match="busy"):
            with runtime.handover("chan:1"):
                pass
    assert log == ["release b", "reacquire a", "reacquire b"]


def test_non_yielding_holder_blocks_before_anything_is_released(tmp_path: Path) -> None:
    log: list[str] = []
    make = lambda n: _Holder(n, log) if n == "a" else _Plain()  # noqa: E731
    with _session(tmp_path, make) as runtime:
        with pytest.raises(LifecycleError, match="do not implement ResourceYield"):
            with runtime.handover("chan:1"):
                pass
    assert log == []


def test_unknown_resource_lists_claimed_ones(tmp_path: Path) -> None:
    with _session(tmp_path, lambda n: _Holder(n, [])) as runtime:
        with pytest.raises(LifecycleError, match="claimed resources: chan:1"):
            runtime.handover("nope").__enter__()


def test_components_can_hand_over_through_their_context(tmp_path: Path) -> None:
    log: list[str] = []
    seen = {}

    class Self(_Holder):
        pass

    class Fac:
        @contextmanager
        def open(self, context, dependencies, config):
            holder = Self("a", log)
            seen["ctx"] = context
            yield holder

    session = RuntimeSession(
        plan=RuntimePlan(components=(ComponentSpec("a", "t", claims=(ResourceClaim("r"),)),)),
        factories={"t": Fac()},
        evidence=LocalEvidenceStore(tmp_path / "e"),
    )
    with session:
        with seen["ctx"].handover("r"):
            log.append("tool")
    assert log == ["release a", "tool", "reacquire a"]


def test_nested_handover_of_same_resource_is_rejected(tmp_path: Path) -> None:
    with _session(tmp_path, lambda n: _Holder(n, [])) as runtime:
        with runtime.handover("chan:1"):
            with pytest.raises(LifecycleError, match="already handed over"):
                runtime.handover("chan:1").__enter__()


def test_release_evidence_failure_reacquires_released_holder(tmp_path, monkeypatch) -> None:
    log = []
    session = _session(tmp_path, lambda n: _Holder(n, log))
    original_event = session.evidence.event

    def broken_event(name, **fields):
        if name == "resource_released":
            raise OSError("release evidence failed")
        original_event(name, **fields)

    try:
        with session as runtime:
            monkeypatch.setattr(session.evidence, "event", broken_event)
            with pytest.raises(OSError, match="release evidence failed"):
                with runtime.handover("chan:1"):
                    pytest.fail("tool ran after release evidence failure")
            assert log == ["release b", "reacquire b"]
    finally:
        monkeypatch.setattr(session.evidence, "event", original_event)
        session.evidence.close(outcome="failed")


def test_reacquisition_failure_recording_cannot_skip_other_holders(tmp_path, monkeypatch) -> None:
    log = []

    class Holder(_Holder):
        def reacquire(self):
            super().reacquire()
            if self.name == "a":
                raise RuntimeError("reacquisition failed")

    session = _session(tmp_path, lambda n: Holder(n, log))
    original_event = session.evidence.event

    def broken_event(name, **fields):
        if name == "resource_reacquire_failed":
            raise OSError("failure recording failed")
        original_event(name, **fields)

    try:
        with pytest.raises(LifecycleError, match="not recovered"):
            with session as runtime:
                monkeypatch.setattr(session.evidence, "event", broken_event)
                with pytest.raises(LifecycleError, match="reacquisition failed"):
                    with runtime.handover("chan:1"):
                        pass
                assert log[-2:] == ["reacquire a", "reacquire b"]
    finally:
        monkeypatch.setattr(session.evidence, "event", original_event)
        session.evidence.close(outcome="failed")


def test_multiple_reacquisition_failures_retain_secondary_diagnostics(tmp_path):
    errors = {name: OSError(f"recovery:{name}") for name in ("a", "b")}
    log = []

    class Holder(_Holder):
        def reacquire(self):
            super().reacquire()
            raise errors[self.name]

    session = _session(tmp_path, lambda name: Holder(name, log))
    try:
        with pytest.raises(LifecycleError, match="not recovered"):
            with session as runtime:
                with pytest.raises(LifecycleError) as failure:
                    with runtime.handover("chan:1"):
                        pass
                assert failure.value.__cause__ is errors["a"]
                assert "recovery:b" in " ".join(failure.value.__notes__)
                assert log[-2:] == ["reacquire a", "reacquire b"]
    finally:
        session.evidence.close()
