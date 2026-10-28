"""Negative regressions for defects confirmed in the release review."""

import json
import subprocess
import sys
from contextlib import contextmanager

import pytest

from autospan.api import ComponentSpec, GateContribution, LazyProvider, ProviderContribution
from autospan.api.errors import ConfigurationError, LifecycleError
from autospan._core.config import apply_overrides, load_config
from autospan._core.evidence_store import LocalEvidenceStore
from autospan._core.providers import ProviderComponentFactory
from autospan.capabilities.flasher import FLASHER
from autospan.testing.conformance import check_debugger


def test_invalid_lazy_acquisition_closes_before_retry():
    events = []

    class Factory:
        @contextmanager
        def open(self, context, dependencies, config):
            events.append("open")
            try:
                yield object()
            finally:
                events.append("close")

    factory = ProviderComponentFactory(
        {("flasher", "fake"): ProviderContribution(FLASHER, "fake", Factory())}
    )
    with factory.open(
        None, {}, dict(contract="flasher", provider="fake", options={}, lazy=True)
    ) as lazy:
        for index in range(2):
            with pytest.raises(LifecycleError):
                lazy.resolve()
            assert events == ["open", "close"] * (index + 1)
    assert events == ["open", "close"] * 2
    with pytest.raises(LifecycleError, match="closed"):
        lazy.resolve()


def test_invalid_lazy_cleanup_preserves_validation_error_and_prevents_retry():
    events = []

    class Factory:
        @contextmanager
        def open(self, context, dependencies, config):
            events.append("open")
            try:
                yield object()
            finally:
                raise OSError("cleanup failed")

    factory = ProviderComponentFactory(
        {("flasher", "fake"): ProviderContribution(FLASHER, "fake", Factory())}
    )
    with factory.open(
        None, {}, dict(contract="flasher", provider="fake", options={}, lazy=True)
    ) as lazy:
        with pytest.raises(LifecycleError, match="does not satisfy") as caught:
            lazy.resolve()
        assert "cleanup failed" in " ".join(caught.value.__notes__)
        with pytest.raises(LifecycleError, match="cleanup"):
            lazy.resolve()
        assert events == ["open"]


@pytest.mark.parametrize("field", ["gates", "dependencies"])
def test_component_rejects_string_name_collection(field):
    with pytest.raises(TypeError, match=field):
        ComponentSpec("fake", "fake", **{field: "hardware"})


def test_gate_rejects_string_implications():
    with pytest.raises(TypeError, match="implies"):
        GateContribution("flashing", implies="hardware")


@pytest.mark.parametrize("text", ["a: 1\na: 2", "a: {b: 1, b: 2}", "true: first\n1: second"])
def test_duplicate_yaml_rejected_with_location(tmp_path, text):
    path = tmp_path / "duplicate.yaml"
    path.write_text(text)
    with pytest.raises(ConfigurationError, match="duplicate") as caught:
        load_config(path)
    assert str(path) in str(caught.value) and "line" in str(caught.value)


def test_override_yaml_rejects_duplicates():
    with pytest.raises(ConfigurationError, match="duplicate"):
        apply_overrides({}, ["bench={port: first, port: second}"])


def test_yaml_merge_override_is_valid(tmp_path):
    path = tmp_path / "merge.yaml"
    path.write_text("base: &base {port: first, speed: 1}\nbench: {<<: *base, port: second}\n")
    assert load_config(path)["bench"] == {"port": "second", "speed": 1}


def test_debugger_conformance_does_not_call_methods_by_default():
    from autospan.capabilities.debugger.fake import FakeDebugger

    class Spy(FakeDebugger):
        def halt(self):
            raise AssertionError("must not halt")

    check_debugger(Spy())


def test_flasher_checks_survive_optimized_python():
    script = """
from autospan.testing.conformance import check_flasher
class Fake:
    def flash(self, request): return object()
try:
    check_flasher(Fake(), allow_flash=True)
except AssertionError:
    pass
else:
    raise RuntimeError('invalid result accepted')
"""
    result = subprocess.run(
        [sys.executable, "-O", "-c", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr


def test_step_identity_is_unique_and_nested_without_changing_legacy_label(tmp_path):
    store = LocalEvidenceStore(tmp_path)
    try:
        with store.step("same") as parent:
            with store.step("same") as child:
                with store.reserve_artifact(kind="trace", source="fake") as artifact:
                    artifact.path.write_bytes(b"fake")
        with store.step("same") as sibling:
            pass
        assert parent.step_id == child.step_id == sibling.step_id == "same"
        assert len({parent.step_uid, child.step_uid, sibling.step_uid}) == 3
        assert child.parent_step_uid == parent.step_uid
        assert sibling.parent_step_uid is None
        assert store.artifacts()[0].context.step_uid == child.step_uid
    finally:
        store.close()
    events = [json.loads(line) for line in (store.root / "events.jsonl").read_text().splitlines()]
    assert all(event["schema_version"] == 2 for event in events)
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))


def test_lazy_provider_serializes_first_resolution():
    from threading import Barrier, Event, Thread

    ready, release = Event(), Event()
    barrier = Barrier(3)
    opens, values, errors = [], [], []
    instance = object()

    def opener():
        opens.append(1)
        ready.set()
        if not release.wait(3):
            raise RuntimeError("test opener timeout")
        return instance

    lazy = LazyProvider(opener)

    def resolve():
        try:
            barrier.wait(timeout=3)
            values.append(lazy.resolve())
        except BaseException as exc:
            errors.append(exc)

    threads = [Thread(target=resolve) for _ in range(2)]
    for thread in threads:
        thread.start()
    try:
        barrier.wait(timeout=3)
        assert ready.wait(3)
    finally:
        release.set()
        for thread in threads:
            thread.join(3)
    assert not errors and not any(thread.is_alive() for thread in threads)
    assert opens == [1] and values == [instance, instance]


def test_lazy_open_failure_can_be_explicitly_retried():
    calls = []
    instance = object()

    def opener():
        calls.append(1)
        if len(calls) == 1:
            raise KeyboardInterrupt("opening interrupted")
        return instance

    lazy = LazyProvider(opener)
    with pytest.raises(KeyboardInterrupt):
        lazy.resolve()
    assert not lazy.opened
    assert lazy.resolve() is instance and len(calls) == 2


def test_flash_requires_opt_in_before_invoking_driver():
    from autospan.testing.conformance import check_flasher

    class Fake:
        def flash(self, request):
            raise AssertionError("must not execute")

    with pytest.raises(ValueError, match="allow_flash"):
        check_flasher(Fake())


def test_debugger_scratch_restored_after_failed_check():
    from autospan.capabilities.debugger.fake import FakeDebugger

    class Fake(FakeDebugger):
        def read_variable(self, name):
            value = super().read_variable(name)
            return "wrong" if value == 7 else value

    fake = Fake(variables={"scratch": 3})
    with pytest.raises(AssertionError, match="round-trip"):
        check_debugger(fake, allow_control=True, scratch_variable="scratch")
    assert fake.read_variable("scratch") == 3 and not fake.is_running()


def test_concurrent_nested_steps_keep_unique_identity_and_bounded_paths(tmp_path):
    from threading import Thread

    store = LocalEvidenceStore(tmp_path)
    errors, contexts = [], []
    try:
        with store.step("parent") as parent:

            def worker():
                try:
                    with store.bind_context(parent), store.step("測" * 1000) as child:
                        contexts.append(child)
                        with store.reserve_artifact(kind="trace", source="fake") as reserved:
                            reserved.path.write_text("ok")
                except BaseException as exc:
                    errors.append(exc)

            threads = [Thread(target=worker) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(3)
            assert not any(thread.is_alive() for thread in threads)
        assert not errors
        assert len({context.step_uid for context in contexts}) == 8
        assert {context.parent_step_uid for context in contexts} == {parent.step_uid}
        assert len({artifact.path.parent for artifact in store.artifacts()}) == 8
    finally:
        store.close()


def test_recursive_yaml_merge_is_rejected(tmp_path):
    path = tmp_path / "recursive.yaml"
    path.write_text("a: &a {<<: *a}")
    with pytest.raises(ConfigurationError, match="recursive YAML merge"):
        load_config(path)


def test_literal_merge_and_value_keys_are_valid_yaml(tmp_path):
    path = tmp_path / "literal.yaml"
    path.write_text('base: &base {port: first}\nbench: {<<: *base, "<<": literal, =: value}\n')
    assert load_config(path)["bench"] == {"port": "first", "<<": "literal", "=": "value"}


def test_eager_validation_error_survives_cleanup_failure():
    class Factory:
        @contextmanager
        def open(self, context, dependencies, config):
            try:
                yield object()
            finally:
                raise OSError("cleanup failed")

    factory = ProviderComponentFactory(
        {("flasher", "fake"): ProviderContribution(FLASHER, "fake", Factory())}
    )
    with pytest.raises(LifecycleError, match="does not satisfy") as caught:
        with factory.open(None, {}, dict(contract="flasher", provider="fake", options={})):
            raise AssertionError("invalid provider must not be yielded")
    assert "cleanup failed" in " ".join(caught.value.__notes__)
