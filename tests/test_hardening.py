from __future__ import annotations

import json
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

from autospan.api import ComponentSpec, PluginError, RuntimePlan
from autospan.api.evidence import EvidenceContext
from autospan._core.evidence_store import LocalEvidenceStore
from autospan._core.host import HostBuild
from autospan.frontends.pytest_plugin import AutoSpanPytestBridge
from autospan.frontends.test_command import run_tests


def test_autospan_test_refuses_implicit_pytest_discovery(tmp_path: Path) -> None:
    host = HostBuild(plugins=(), plan=RuntimePlan(components=()), factories={}, commands=())
    with pytest.raises(PluginError, match="No scenarios were contributed"):
        run_tests(host=host, evidence_root=tmp_path)


def test_plan_is_deeply_immutable() -> None:
    source = {"nested": {"items": [1, {"value": 2}]}}
    plan = RuntimePlan(
        components=(ComponentSpec("x", "demo", config=source),),
        metadata={"nested": {"items": [1, 2]}},
    )

    source["nested"]["items"].append(3)  # type: ignore[index,union-attr]
    assert tuple(plan.components[0].config["nested"]["items"]) == (1, {"value": 2})

    with pytest.raises(TypeError):
        plan.components[0].config["nested"]["new"] = 3  # type: ignore[index]
    with pytest.raises(TypeError):
        plan.metadata["nested"]["new"] = 3  # type: ignore[index]


def test_pytest_phase_reports_keep_test_correlation_after_fixture_scope(tmp_path: Path) -> None:
    evidence = LocalEvidenceStore(tmp_path)
    host = HostBuild(plugins=(), plan=RuntimePlan(components=()), factories={}, commands=())
    bridge = AutoSpanPytestBridge(host, evidence)
    try:
        bridge.pytest_runtest_logreport(
            SimpleNamespace(
                nodeid="pkg/test_demo.py::test_x",
                when="teardown",
                outcome="passed",
                duration=0.01,
            )
        )
    finally:
        evidence.close(outcome="passed")

    events = [
        json.loads(line) for line in (evidence.root / "events.jsonl").read_text().splitlines()
    ]
    teardown = next(
        item
        for item in events
        if item["name"] == "pytest_phase_finished" and item["attributes"]["phase"] == "teardown"
    )
    assert teardown["test_id"] == "pkg/test_demo.py::test_x"


def test_evidence_context_can_be_explicitly_propagated_to_worker_threads(tmp_path: Path) -> None:
    evidence = LocalEvidenceStore(tmp_path)
    context = EvidenceContext(evidence.run_id, test_id="worker-test", step_id="capture")

    def worker(index: int) -> None:
        with evidence.bind_context(context):
            for count in range(10):
                evidence.event("worker_event", source="worker", worker=index, count=count)

    threads = [Thread(target=worker, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    evidence.close(outcome="passed")

    events = [
        json.loads(line) for line in (evidence.root / "events.jsonl").read_text().splitlines()
    ]
    worker_events = [item for item in events if item["name"] == "worker_event"]
    assert len(worker_events) == 40
    assert {item["test_id"] for item in worker_events} == {"worker-test"}
    assert {item["step_id"] for item in worker_events} == {"capture"}


def test_evidence_step_creates_correlated_events(tmp_path: Path) -> None:
    evidence = LocalEvidenceStore(tmp_path)
    with evidence.scope(test_id="scenario::test"):
        with evidence.step("Enter extended session"):
            evidence.event("inside")
    evidence.close(outcome="passed")

    events = [
        json.loads(line) for line in (evidence.root / "events.jsonl").read_text().splitlines()
    ]
    inside = next(item for item in events if item["name"] == "inside")
    assert inside["test_id"] == "scenario::test"
    assert inside["step_id"] == "Enter extended session"
