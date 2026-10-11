from collections.abc import Mapping
from contextlib import contextmanager
from typing import Any, Iterator

import pytest

from autospan.api import ComponentSpec, LifecycleError, RuntimePlan
from autospan._core.evidence_store import LocalEvidenceStore
from autospan._core.runtime import RuntimeSession


class RecordingFactory:
    def __init__(self, name: str, log: list[str], *, fail: bool = False) -> None:
        self.name = name
        self.log = log
        self.fail = fail

    @contextmanager
    def open(
        self,
        context: Any,
        dependencies: Mapping[str, object],
        config: Mapping[str, Any],
    ) -> Iterator[object]:
        del context, dependencies, config
        self.log.append(f"open:{self.name}")
        if self.fail:
            raise RuntimeError(f"boom:{self.name}")
        try:
            yield object()
        finally:
            self.log.append(f"close:{self.name}")


def test_runtime_opens_dependency_first_and_closes_reverse(tmp_path):
    log: list[str] = []
    plan = RuntimePlan(
        components=(
            ComponentSpec("a", "a"),
            ComponentSpec("b", "b", dependencies=("a",)),
            ComponentSpec("c", "c", dependencies=("b",)),
        )
    )
    evidence = LocalEvidenceStore(tmp_path / "evidence")
    try:
        with RuntimeSession(
            plan=plan,
            factories={
                "a": RecordingFactory("a", log),
                "b": RecordingFactory("b", log),
                "c": RecordingFactory("c", log),
            },
            evidence=evidence,
        ):
            assert log == ["open:a", "open:b", "open:c"]
    finally:
        evidence.close(outcome="passed")
    assert log == ["open:a", "open:b", "open:c", "close:c", "close:b", "close:a"]


def test_partial_startup_rolls_back_already_open_components(tmp_path):
    log: list[str] = []
    plan = RuntimePlan(
        components=(
            ComponentSpec("a", "a"),
            ComponentSpec("b", "b", dependencies=("a",)),
            ComponentSpec("c", "c", dependencies=("b",)),
        )
    )
    evidence = LocalEvidenceStore(tmp_path / "evidence")
    try:
        with pytest.raises(RuntimeError, match="boom:c"):
            with RuntimeSession(
                plan=plan,
                factories={
                    "a": RecordingFactory("a", log),
                    "b": RecordingFactory("b", log),
                    "c": RecordingFactory("c", log, fail=True),
                },
                evidence=evidence,
            ):
                pass
    finally:
        evidence.close(outcome="failed")
    assert log == ["open:a", "open:b", "open:c", "close:b", "close:a"]


@pytest.mark.parametrize("event_name", ["component_opened", "runtime_started"])
def test_startup_evidence_failure_rolls_back_components(tmp_path, monkeypatch, event_name):
    log = []
    evidence = LocalEvidenceStore(tmp_path / "evidence")
    original_event = evidence.event

    def broken_event(name, **fields):
        if name == event_name:
            raise OSError("evidence disk failed")
        original_event(name, **fields)

    session = RuntimeSession(
        plan=RuntimePlan(components=(ComponentSpec("a", "a"),)),
        factories={"a": RecordingFactory("a", log)},
        evidence=evidence,
    )
    monkeypatch.setattr(evidence, "event", broken_event)
    try:
        with pytest.raises(OSError, match="evidence disk failed"):
            with session:
                pytest.fail("runtime entered despite evidence failure")
        assert log == ["open:a", "close:a"]
    finally:
        monkeypatch.setattr(evidence, "event", original_event)
        session._stack.close()
        evidence.close(outcome="failed")


def test_startup_failure_survives_failure_recording_and_still_rolls_back(tmp_path, monkeypatch):
    log = []
    evidence = LocalEvidenceStore(tmp_path / "evidence")
    original_event = evidence.event

    def broken_event(name, **fields):
        if name == "runtime_start_failed":
            raise OSError("evidence disk failed")
        original_event(name, **fields)

    session = RuntimeSession(
        plan=RuntimePlan(components=(ComponentSpec("a", "a"), ComponentSpec("b", "b"))),
        factories={"a": RecordingFactory("a", log), "b": RecordingFactory("b", log, fail=True)},
        evidence=evidence,
    )
    monkeypatch.setattr(evidence, "event", broken_event)
    try:
        with pytest.raises(RuntimeError, match="boom:b"):
            with session:
                pass
        assert log == ["open:a", "open:b", "close:a"]
    finally:
        monkeypatch.setattr(evidence, "event", original_event)
        session._stack.close()
        evidence.close(outcome="failed")


@pytest.mark.parametrize("body_fails", [False, True])
def test_shutdown_evidence_failure_cannot_skip_component_cleanup(tmp_path, monkeypatch, body_fails):
    log = []
    evidence = LocalEvidenceStore(tmp_path / "evidence")
    original_event = evidence.event
    body_error = ValueError("scenario failed")

    def broken_event(name, **fields):
        if name == "runtime_stopping":
            raise OSError("evidence disk failed")
        original_event(name, **fields)

    session = RuntimeSession(
        plan=RuntimePlan(components=(ComponentSpec("a", "a"),)),
        factories={"a": RecordingFactory("a", log)},
        evidence=evidence,
    )
    monkeypatch.setattr(evidence, "event", broken_event)
    try:
        with pytest.raises(ValueError if body_fails else LifecycleError) as failure:
            with session:
                if body_fails:
                    raise body_error
        if body_fails:
            assert failure.value is body_error
        assert log == ["open:a", "close:a"]
    finally:
        monkeypatch.setattr(evidence, "event", original_event)
        session._stack.close()
        evidence.close(outcome="failed")


@pytest.mark.parametrize(
    "phase,body_fails", [("startup", True), ("shutdown", True), ("shutdown", False)]
)
def test_multiple_cleanup_failures_preserve_every_error_and_original_cause(
    tmp_path, phase, body_fails
):
    log = []
    evidence = LocalEvidenceStore(tmp_path / "evidence")
    original = ValueError("original failure")

    class Factory:
        def __init__(self, name):
            self.name = name

        @contextmanager
        def open(self, context, dependencies, config):
            log.append(f"open:{self.name}")
            if self.name == "c":
                raise original
            try:
                yield object()
            finally:
                log.append(f"close:{self.name}")
                raise RuntimeError(f"cleanup:{self.name}")

    specs = [ComponentSpec("a", "a"), ComponentSpec("b", "b", dependencies=("a",))]
    if phase == "startup":
        specs.append(ComponentSpec("c", "c", dependencies=("b",)))
    try:
        with pytest.raises(ValueError if body_fails else LifecycleError) as failure:
            with RuntimeSession(
                plan=RuntimePlan(tuple(specs)),
                factories={name: Factory(name) for name in ("a", "b", "c")},
                evidence=evidence,
            ):
                if body_fails:
                    raise original
        if body_fails:
            assert failure.value is original
            details = " ".join(original.__notes__)
        else:
            details = str(failure.value)
        assert "cleanup:a" in details and "cleanup:b" in details
        assert log[-2:] == ["close:b", "close:a"]
    finally:
        evidence.close(outcome="failed")
