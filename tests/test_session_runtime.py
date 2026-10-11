from contextlib import contextmanager
import json

import pytest

from autospan.api import ComponentSpec, ResourceClaim, RuntimePlan, ScenarioContribution
from autospan._core.host import HostBuild
from autospan._core.evidence_store import LocalEvidenceStore
from autospan.frontends.pytest_plugin import AutoSpanPytestBridge
from autospan.frontends.test_command import run_tests


@pytest.mark.parametrize("failure,expected", [(False, "passed"), (True, "failed")])
def test_session_runtime_opens_once_and_reports_aggregate_outcome(
    tmp_path, monkeypatch, failure, expected
):
    name = "session_scenarios_" + tmp_path.name.replace("-", "_")
    package = tmp_path / name
    package.mkdir()
    (package / "__init__.py").touch()
    (package / f"test_{name}.py").write_text(
        "def test_first(session_runtime):\n    assert session_runtime.get('bench') == 42\n"
        "def test_second(session_runtime):\n    assert session_runtime.get('bench') == 42\n"
        + ("    assert False\n" if failure else ""),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    calls = []

    class Factory:
        @contextmanager
        def open(self, context, dependencies, config):
            calls.append("open")
            yield 42
            calls.append(context.outcome)

    host = HostBuild(
        plugins=(),
        plan=RuntimePlan(
            components=(ComponentSpec("bench", "bench"),),
            scenarios=(ScenarioContribution(name, "."),),
        ),
        factories={"bench": Factory()},
        commands=(),
    )
    code = run_tests(host=host, evidence_root=tmp_path / "evidence", pytest_args=("-q",))
    assert code == int(failure)
    assert calls == ["open", expected]


def test_session_runtime_gate_prevents_hardware_open(tmp_path, monkeypatch):
    name = "session_gated_" + tmp_path.name.replace("-", "_")
    package = tmp_path / name
    package.mkdir()
    (package / "__init__.py").touch()
    (package / f"test_{name}.py").write_text("def test_bench(session_runtime):\n    assert False\n")
    monkeypatch.syspath_prepend(str(tmp_path))

    class Factory:
        def open(self, *args):
            raise AssertionError("ungated hardware opened")

    host = HostBuild(
        plugins=(),
        plan=RuntimePlan(
            components=(ComponentSpec("bench", "bench", gates=("hardware",)),),
            scenarios=(ScenarioContribution(name, "."),),
        ),
        factories={"bench": Factory()},
        commands=(),
    )
    assert run_tests(host=host, evidence_root=tmp_path / "evidence", pytest_args=("-q",)) == 0


def test_mixed_fixture_scopes_cannot_open_one_exclusive_bench_twice(tmp_path, monkeypatch, capsys):
    name = "mixed_scopes_" + tmp_path.name.replace("-", "_")
    package = tmp_path / name
    package.mkdir()
    (package / "__init__.py").touch()
    (package / f"test_{name}.py").write_text(
        "def test_conflict(session_runtime, runtime):\n"
        "    raise AssertionError('both runtimes opened')\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    calls = []

    class Factory:
        @contextmanager
        def open(self, context, dependencies, config):
            calls.append("open")
            yield object()
            calls.append(context.outcome)

    host = HostBuild(
        plugins=(),
        plan=RuntimePlan(
            components=(ComponentSpec("bench", "bench", claims=(ResourceClaim("bench:1"),)),),
            scenarios=(ScenarioContribution(name, "."),),
        ),
        factories={"bench": Factory()},
        commands=(),
    )
    root = tmp_path / "evidence"
    assert run_tests(host=host, evidence_root=root, pytest_args=("-q",)) == 1
    assert "already claimed" in capsys.readouterr().out
    assert calls == ["open", "failed"]
    (manifest,) = root.glob("*/manifest.json")
    assert json.loads(manifest.read_text())["outcome"] == "failed"


@pytest.mark.parametrize("phase", ["setup", "teardown", "session_teardown", "interrupt"])
def test_session_runtime_outcome_includes_fixture_failures(tmp_path, monkeypatch, phase):
    name = "session_fixture_failure_" + tmp_path.name.replace("-", "_")
    package = tmp_path / name
    package.mkdir()
    (package / "__init__.py").touch()
    scope = "session" if phase == "session_teardown" else "function"
    failure = "    raise RuntimeError('fixture failed')\n"
    fixture_body = failure if phase == "setup" else "    yield\n" + failure
    test_body = "    pass\n"
    if phase == "interrupt":
        fixture_body = "    yield\n"
        test_body = "    raise KeyboardInterrupt\n"
    (package / f"test_{name}.py").write_text(
        "import pytest\n"
        f"@pytest.fixture(scope={scope!r})\n"
        "def broken(session_runtime):\n" + fixture_body + "def test_case(broken):\n" + test_body,
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    outcomes = []

    class Factory:
        @contextmanager
        def open(self, context, dependencies, config):
            yield 42
            outcomes.append(context.outcome)

    host = HostBuild(
        plugins=(),
        plan=RuntimePlan(
            components=(ComponentSpec("bench", "bench"),),
            scenarios=(ScenarioContribution(name, "."),),
        ),
        factories={"bench": Factory()},
        commands=(),
    )
    root = tmp_path / "evidence"
    assert run_tests(host=host, evidence_root=root, pytest_args=("-q",)) == (
        2 if phase == "interrupt" else 1
    )
    assert outcomes == ["failed"]
    (manifest,) = root.glob("*/manifest.json")
    assert json.loads(manifest.read_text())["outcome"] == "failed"


def test_session_cleanup_failure_fails_exit_code_and_manifest(tmp_path, monkeypatch):
    name = "session_cleanup_" + tmp_path.name.replace("-", "_")
    package = tmp_path / name
    package.mkdir()
    (package / "__init__.py").touch()
    (package / f"test_{name}.py").write_text("def test_case(session_runtime):\n    pass\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    calls = []

    class Factory:
        @contextmanager
        def open(self, context, dependencies, config):
            calls.append("open")
            yield object()
            calls.append("close")
            raise RuntimeError("component cleanup failed")

    host = HostBuild(
        plugins=(),
        plan=RuntimePlan(
            components=(ComponentSpec("bench", "bench"),),
            scenarios=(ScenarioContribution(name, "."),),
        ),
        factories={"bench": Factory()},
        commands=(),
    )
    root = tmp_path / "evidence"
    assert run_tests(host=host, evidence_root=root, pytest_args=("-q",)) == 1
    assert calls == ["open", "close"]
    (manifest,) = root.glob("*/manifest.json")
    assert json.loads(manifest.read_text())["outcome"] == "failed"


def test_pytest_abort_preserves_cause_and_finalizes_evidence(tmp_path, monkeypatch):
    name = "aborted_scenarios_" + tmp_path.name.replace("-", "_")
    package = tmp_path / name
    package.mkdir()
    (package / "__init__.py").touch()
    monkeypatch.syspath_prepend(str(tmp_path))
    host = HostBuild((), RuntimePlan((), scenarios=(ScenarioContribution(name, "."),)), {}, ())
    error = KeyboardInterrupt("pytest aborted")
    calls = []
    original_close = LocalEvidenceStore.close

    def aborted_main(*args, **kwargs):
        raise error

    def close_runtime(self, exit_code):
        calls.append(exit_code)
        raise RuntimeError("owner cleanup failed")

    def close_evidence(self, **kwargs):
        original_close(self, **kwargs)
        raise OSError("evidence finalization failed")

    monkeypatch.setattr(pytest, "main", aborted_main)
    monkeypatch.setattr(AutoSpanPytestBridge, "close_session_runtime", close_runtime)
    monkeypatch.setattr(LocalEvidenceStore, "close", close_evidence)
    root = tmp_path / "evidence"
    with pytest.raises(KeyboardInterrupt) as failure:
        run_tests(host=host, evidence_root=root)
    assert failure.value is error
    assert calls == [1]
    assert any("owner cleanup failed" in note for note in error.__notes__)
    assert any("evidence finalization failed" in note for note in error.__notes__)
    (manifest,) = root.glob("*/manifest.json")
    assert json.loads(manifest.read_text())["outcome"] == "failed"
