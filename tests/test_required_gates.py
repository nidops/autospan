"""Required verification is independent of optional hardware authorization."""

import json
from contextlib import contextmanager
from uuid import uuid4

import pytest

from autospan.api import ComponentSpec, RuntimePlan, ScenarioContribution
from autospan._core.host import HostBuild
from autospan.frontends.test_command import run_tests


def invoke(tmp_path, monkeypatch, source, args=(), *, gated=False, cli=False, conftest=""):
    package = "coverage_" + uuid4().hex
    root = tmp_path / package
    root.mkdir()
    (root / "__init__.py").write_text("")
    scenarios = root / "scenarios"
    scenarios.mkdir()
    (scenarios / "__init__.py").write_text("")
    (scenarios / "conftest.py").write_text(conftest)
    (scenarios / "test_probe.py").write_text(source)
    monkeypatch.syspath_prepend(str(tmp_path))
    calls = []

    class Fake:
        @contextmanager
        def open(self, context, dependencies, config):
            calls.append("open")
            try:
                yield object()
            finally:
                calls.append("close")

    host = HostBuild(
        (),
        RuntimePlan(
            components=(ComponentSpec("fake", "fake", gates=("hardware",)),) if gated else (),
            scenarios=(ScenarioContribution(package),),
        ),
        {"fake": Fake()},
        (),
    )
    if cli:
        from autospan.frontends import cli as frontend

        config = tmp_path / "config.yaml"
        config.write_text("autospan: {plugins: []}")
        monkeypatch.setattr(frontend, "build_host", lambda **kwargs: host)
        code = frontend.main(
            [
                "test",
                "--config",
                str(config),
                "--evidence-dir",
                str(tmp_path / "evidence"),
                "--",
                "-q",
                *args,
            ]
        )
    else:
        code = run_tests(host=host, evidence_root=tmp_path / "evidence", pytest_args=("-q", *args))
    events_file = next((tmp_path / "evidence").glob("*/events.jsonl"))
    events = [json.loads(line) for line in events_file.read_text().splitlines()]
    return code, calls, events


def test_required_gate_never_implies_authorization(tmp_path, monkeypatch):
    code, calls, events = invoke(
        tmp_path,
        monkeypatch,
        "def test_hw(runtime): pass",
        ("--require-gate", "hardware"),
        gated=True,
    )
    assert code == 4 and calls == []


def test_authorized_fake_runtime_counts_and_tears_down(tmp_path, monkeypatch):
    code, calls, events = invoke(
        tmp_path,
        monkeypatch,
        "def test_hw(runtime): pass",
        ("--hardware", "--require-gate", "hardware"),
        gated=True,
    )
    assert code == 0 and calls == ["open", "close"]
    coverage = next(event for event in events if event["name"] == "required_gates_checked")
    assert coverage["attributes"]["passed"] == {"hardware": 1}


@pytest.mark.parametrize(
    "body",
    [
        "import pytest\n@pytest.mark.hardware\n@pytest.mark.skip(reason='offline')\ndef test_hw(): pass",
        "import pytest\n@pytest.mark.hardware\n@pytest.mark.xfail\ndef test_hw(): assert False",
        "import pytest\n@pytest.mark.hardware\n@pytest.mark.xfail\ndef test_hw(): pass",
        "import pytest\n@pytest.mark.hardware\ndef test_hw(): pytest.skip('not verified')",
    ],
)
def test_unverified_hardware_cannot_be_hidden_by_passing_software(tmp_path, monkeypatch, body):
    code, _, _ = invoke(
        tmp_path,
        monkeypatch,
        body + "\ndef test_software(): pass\n",
        ("--hardware", "--require-gate", "hardware"),
    )
    assert code == 1


def test_optional_skips_keep_normal_pytest_success(tmp_path, monkeypatch):
    code, calls, _ = invoke(tmp_path, monkeypatch, "def test_hw(runtime): pass", gated=True)
    assert code == 0 and not calls


def test_ordinary_skip_remains_optional(tmp_path, monkeypatch):
    code, _, _ = invoke(
        tmp_path, monkeypatch, "import pytest\n@pytest.mark.skip\ndef test_optional(): pass"
    )
    assert code == 0


def test_no_tests_preserves_pytest_exit_five(tmp_path, monkeypatch):
    code, _, _ = invoke(tmp_path, monkeypatch, "", ("--hardware", "--require-gate", "hardware"))
    assert code == 5


def test_unknown_required_gate_is_usage_error(tmp_path, monkeypatch):
    code, calls, _ = invoke(
        tmp_path, monkeypatch, "def test_ok(): pass", ("--require-gate", "typo")
    )
    assert code == 4 and not calls


def test_each_required_gate_needs_coverage(tmp_path, monkeypatch):
    code, _, _ = invoke(
        tmp_path,
        monkeypatch,
        "import pytest\n@pytest.mark.hardware\ndef test_hw(): pass",
        (
            "--hardware",
            "--destructive",
            "--require-gate",
            "hardware",
            "--require-gate",
            "destructive",
        ),
    )
    assert code == 1


def test_required_gate_refuses_distributed_worker_configuration(tmp_path):
    from types import SimpleNamespace
    from autospan._core.evidence_store import LocalEvidenceStore
    from autospan.frontends.pytest_plugin import AutoSpanPytestBridge

    store = LocalEvidenceStore(tmp_path)
    bridge = AutoSpanPytestBridge(HostBuild((), RuntimePlan(()), {}, ()), store)
    config = SimpleNamespace(
        option=SimpleNamespace(numprocesses=2), getoption=lambda name: ["hardware"]
    )
    try:
        with pytest.raises(pytest.UsageError, match="xdist"):
            bridge.pytest_configure(config)
    finally:
        store.close()


def test_cli_propagates_required_coverage_failure(tmp_path, monkeypatch):
    code, _, events = invoke(
        tmp_path,
        monkeypatch,
        "def test_software(): pass",
        ("--hardware", "--require-gate", "hardware"),
        cli=True,
    )
    assert code == 1
    assert (
        next(event for event in events if event["name"] == "run_finished")["attributes"]["outcome"]
        == "failed"
    )


def test_required_coverage_failure_reaches_shared_runtime_cleanup(tmp_path, monkeypatch):
    code, _, events = invoke(
        tmp_path,
        monkeypatch,
        "def test_software(session_runtime): pass",
        ("--hardware", "--require-gate", "hardware"),
    )
    assert code == 1
    stopping = next(event for event in events if event["name"] == "runtime_stopping")
    assert stopping["attributes"]["outcome"] == "failed"


def test_later_pytest_exit_override_cannot_erase_required_gate_failure(tmp_path, monkeypatch):
    code, _, _ = invoke(
        tmp_path,
        monkeypatch,
        "def test_software(): pass",
        ("--hardware", "--require-gate", "hardware"),
        conftest="def pytest_sessionfinish(session):\n    session.exitstatus = 0\n",
    )
    assert code == 1
