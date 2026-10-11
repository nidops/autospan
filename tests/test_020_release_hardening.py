"""Regression checks for execution scope, runtime recovery and evidence closing."""

from contextlib import contextmanager

import pytest

from autospan.api import ClaimMode, ComponentSpec, ResourceClaim, RuntimePlan
from autospan.api.component import ScenarioContribution
from autospan.api.errors import LifecycleError, PluginError
from autospan._core.evidence_store import LocalEvidenceStore
from autospan._core.runtime import RuntimeSession
from autospan.frontends.test_command import resolve_scenario_paths, resolve_selections


def test_scenario_directory_cannot_escape_imported_package(tmp_path, monkeypatch):
    pkg = tmp_path / "safe_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (tmp_path / "outside").mkdir()
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(PluginError, match="escapes package"):
        resolve_scenario_paths((ScenarioContribution("safe_pkg", "../outside"),))


def test_outside_file_selection_is_rejected(tmp_path):
    root = tmp_path / "cases"
    root.mkdir()
    inside = root / "test_ok.py"
    inside.write_text("")
    outside = tmp_path / "test_other.py"
    outside.write_text("")
    with pytest.raises(PluginError, match="outside contributed"):
        resolve_selections((str(outside),), (root,), tmp_path)
    assert resolve_selections((str(inside),), (root,), tmp_path) == [str(inside)]


def test_unrecovered_handover_invalidates_runtime_view(tmp_path):
    events = []

    class Holder:
        def release(self):
            events.append("release")

        def reacquire(self):
            events.append("reacquire")
            raise OSError("bus disconnected")

    class Factory:
        @contextmanager
        def open(self, context, dependencies, config):
            yield Holder()

    store = LocalEvidenceStore(tmp_path)
    session = RuntimeSession(
        plan=RuntimePlan(
            (
                ComponentSpec(
                    "device", "fake", claims=(ResourceClaim("bus:one", ClaimMode.EXCLUSIVE),)
                ),
            )
        ),
        factories={"fake": Factory()},
        evidence=store,
    )
    try:
        with pytest.raises(LifecycleError, match="not recovered"):
            with session as runtime:
                with pytest.raises(ValueError, match="tool failed") as error:
                    with runtime.handover("bus:one"):
                        raise ValueError("tool failed")
                assert "bus disconnected" in " ".join(error.value.__notes__)
                assert events == ["release", "reacquire"]
                session.set_outcome("passed")
                assert session.outcome == "failed"
                with pytest.raises(LifecycleError, match="not recovered"):
                    runtime.get("device")
                with pytest.raises(LifecycleError, match="not recovered"):
                    runtime.names()
                with pytest.raises(LifecycleError, match="not recovered"):
                    with runtime.handover("bus:one"):
                        pass
        with pytest.raises(LifecycleError, match="no longer active"):
            runtime.get("device")
    finally:
        store.close(outcome="failed")


def test_bad_manifest_metadata_allows_retry(tmp_path):
    store = LocalEvidenceStore(tmp_path)
    with pytest.raises(TypeError):
        store.close(metadata={"ambiguous": {1: "a", "name": "b"}})
    assert not store._events_file.closed
    store.close(outcome="failed", metadata={"reason": "invalid metadata corrected"})
    assert (store.root / "manifest.json").is_file()


@pytest.mark.parametrize("identifier", ["../outside", "a/b", "..", "\\windows"])
def test_invalid_evidence_run_id_rejected(tmp_path, identifier):
    with pytest.raises(ValueError, match="run_id"):
        LocalEvidenceStore(tmp_path, run_id=identifier)


def test_manifest_publication_failure_can_be_retried_without_duplicate_finish(
    tmp_path, monkeypatch
):
    import json
    from autospan._core import evidence_store

    store = LocalEvidenceStore(tmp_path)
    real_replace = evidence_store.os.replace
    calls = []

    def fail_once(source, dest):
        calls.append((source, dest))
        if len(calls) == 1:
            raise OSError("disk unavailable")
        return real_replace(source, dest)

    with monkeypatch.context() as patch:
        patch.setattr(evidence_store.os, "replace", fail_once)
        with pytest.raises(OSError, match="disk unavailable"):
            store.close(outcome="failed")
        store.close(outcome="failed")
    assert json.loads((store.root / "manifest.json").read_text())["outcome"] == "failed"
    events = [json.loads(line) for line in (store.root / "events.jsonl").read_text().splitlines()]
    assert [e["name"] for e in events].count("run_finished") == 1
