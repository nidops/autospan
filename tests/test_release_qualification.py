"""Fake-only regressions discovered during final 0.2.0 qualification."""

import json
from contextlib import contextmanager
from uuid import uuid4

import pytest

from autospan.api import (
    ComponentSpec,
    LifecycleError,
    ResourceClaim,
    RuntimePlan,
    ScenarioContribution,
)
from autospan._core.evidence_store import LocalEvidenceStore
from autospan._core.host import HostBuild
from autospan._core.runtime import RuntimeSession
from autospan.frontends.test_command import resolve_scenario_paths, run_tests


@pytest.fixture
def hosted(tmp_path, monkeypatch):
    name = "qualification_" + uuid4().hex
    package = tmp_path / name
    package.mkdir()
    (package / "__init__.py").write_text("")
    cases = package / "cases"
    cases.mkdir()
    (cases / "test_ok.py").write_text("def test_ok(): pass\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    host = HostBuild((), RuntimePlan((), scenarios=(ScenarioContribution(name, "cases"),)), {}, ())
    return host, cases


@pytest.mark.parametrize("form", ["attached", "long", "response", "ini", "environment"])
def test_config_override_forms_rejected(hosted, tmp_path, monkeypatch, form):
    host, cases = hosted
    external = tmp_path / "external.ini"
    external.write_text("[pytest]\n")
    args = ("-c" + str(external),)
    if form == "long":
        args = ("--config-file=" + str(external),)
    elif form == "response":
        response = tmp_path / "args.txt"
        response.write_text("-c\n" + str(external) + "\n")
        args = ("@" + str(response),)
    elif form == "ini":
        args = ("-o", "cache_dir=" + str(tmp_path))
    elif form == "environment":
        monkeypatch.setenv("PYTEST_ADDOPTS", "--rootdir=" + str(tmp_path))
    assert run_tests(host=host, evidence_root=tmp_path / "ev", pytest_args=args) == 4


def test_hosted_boundary_paths_accept_equivalent_spellings(tmp_path):
    from types import SimpleNamespace

    from autospan.frontends.pytest_plugin import AutoSpanPytestBridge

    project = tmp_path / "project"
    project.mkdir()
    (project / "subdir").mkdir()
    scenarios = project / "cases"
    scenarios.mkdir()
    ini = project / "pytest.ini"
    ini.write_text("[pytest]\n", encoding="utf-8")
    alias = project / "subdir" / ".."
    evidence = LocalEvidenceStore(tmp_path / "evidence")
    bridge = AutoSpanPytestBridge(
        HostBuild((), RuntimePlan(()), {}, ()),
        evidence,
        scenario_paths=(scenarios.resolve(),),
        hosted_ini=ini,
        hosted_root=project,
    )
    config = SimpleNamespace(inipath=alias / "pytest.ini", rootpath=alias)
    options = SimpleNamespace(
        inifilename=str(alias / "pytest.ini"),
        pyargs=False,
        confcutdir=None,
        rootdir=str(alias),
        override_ini=[],
        file_or_dir=[],
    )
    try:
        assert bridge._hosted_selections(config, options) == [str(scenarios.resolve())]
        options.rootdir = str(tmp_path)
        with pytest.raises(pytest.UsageError, match="execution boundaries"):
            bridge._hosted_selections(config, options)
    finally:
        evidence.close()


def test_normal_option_values_and_node_selection(hosted, tmp_path):
    host, cases = hosted
    (cases / "test_bad.py").write_text('def test_bad(): raise RuntimeError("must not run")')
    assert (
        run_tests(
            host=host,
            evidence_root=tmp_path / "ev",
            pytest_args=(
                "-k",
                "test_ok",
                "-m",
                "not hardware",
                "--log-file",
                str(tmp_path / "output.log"),
                "--junitxml",
                str(tmp_path / "report.xml"),
                str(cases / "test_ok.py") + "::test_ok",
            ),
        )
        == 0
    )


@pytest.mark.parametrize("selection", ["explicit", "response", "symlink"])
def test_outside_tests_never_import_or_execute(hosted, tmp_path, selection):
    host, cases = hosted
    marker = tmp_path / "executed"
    outside = tmp_path / "test_outside.py"
    outside.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\ndef test_outside(): pass\n"
    )
    args = (str(outside),)
    if selection == "response":
        response = tmp_path / "selection.txt"
        response.write_text(str(outside) + "\n")
        args = ("@" + str(response),)
    elif selection == "symlink":
        (cases / "test_link.py").symlink_to(outside)
        args = ()
    assert run_tests(host=host, evidence_root=tmp_path / "ev", pytest_args=args) != 0
    assert not marker.exists()


def test_contributed_symlink_cannot_escape_package(hosted, tmp_path):
    host, cases = hosted
    outside = tmp_path / "outside"
    outside.mkdir()
    (cases.parent / "escape").symlink_to(outside, target_is_directory=True)
    from autospan.api import PluginError

    with pytest.raises(PluginError, match="escapes package"):
        resolve_scenario_paths((ScenarioContribution(host.plan.scenarios[0].package, "escape"),))


@pytest.mark.parametrize("options", [dict(numprocesses=2), dict(dist="each", tx=["popen"])])
def test_required_gate_rejects_xdist_options(tmp_path, options):
    from types import SimpleNamespace
    from autospan.frontends.pytest_plugin import AutoSpanPytestBridge

    store = LocalEvidenceStore(tmp_path)
    bridge = AutoSpanPytestBridge(HostBuild((), RuntimePlan(()), {}, ()), store)
    config = SimpleNamespace(option=SimpleNamespace(**options), getoption=lambda name: ["hardware"])
    try:
        with pytest.raises(pytest.UsageError, match="does not support xdist"):
            bridge.pytest_configure(config)
    finally:
        store.close()


def test_stale_view_and_delayed_handover_cannot_touch_new_activation(tmp_path):
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

    store = LocalEvidenceStore(tmp_path)
    owner = RuntimeSession(
        plan=RuntimePlan((ComponentSpec("device", "fake", claims=(ResourceClaim("q:device"),)),)),
        factories={"fake": Factory()},
        evidence=store,
    )
    try:
        with owner as old:
            delayed = old.handover("q:device")
        with owner as current:
            for access in (
                old.names,
                lambda: old.get("device"),
                lambda: old.require("device", Holder),
                old.items,
                lambda: old.periodic,
            ):
                with pytest.raises(LifecycleError, match="no longer active"):
                    access()
            with pytest.raises(LifecycleError, match="no longer active"):
                with delayed:
                    pytest.fail("stale handover admitted")
            assert current.names() == ("device",)
        assert log == ["close", "close"]
    finally:
        store.close()


@pytest.mark.parametrize("failure", ["close", "replace", "write", "flush"])
def test_finalization_retry_has_one_finish_and_consistent_verdict(tmp_path, monkeypatch, failure):
    store = LocalEvidenceStore(tmp_path)
    stream = store._events_file

    class Interrupted:
        def __getattr__(self, name):
            return getattr(stream, name)

        def close(self):
            raise KeyboardInterrupt("close interrupted")

        def flush(self):
            if failure == "flush":
                raise OSError("flush failed")
            return stream.flush()

    with monkeypatch.context() as patch:
        if failure in ("close", "flush"):
            patch.setattr(store, "_events_file", Interrupted())
        elif failure == "replace":
            patch.setattr(
                "autospan._core.evidence_store.os.replace",
                lambda *a: (_ for _ in ()).throw(OSError("replace failed")),
            )
        else:
            from pathlib import Path

            patch.setattr(
                Path, "write_text", lambda *a, **kw: (_ for _ in ()).throw(OSError("write failed"))
            )
        with pytest.raises((KeyboardInterrupt, OSError)):
            store.close(outcome="failed", metadata={"reason": "original"})
    # Once run_finished exists, a retry must not change its verdict or metadata.
    store.close(outcome="passed", metadata={"reason": "retry"})
    events = [json.loads(line) for line in (store.root / "events.jsonl").read_text().splitlines()]
    finished = [e for e in events if e["name"] == "run_finished"]
    manifest = json.loads((store.root / "manifest.json").read_text())
    assert len(finished) == 1
    assert manifest["outcome"] == finished[0]["attributes"]["outcome"]
    assert manifest["outcome"] == ("failed" if failure in ("close", "replace") else "passed")
    assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))


@pytest.mark.parametrize(
    "identifier", ["C:escape", "C:", "name.", "bad:name", "CON", "com1.log", "x" * 300]
)
def test_run_ids_are_portable_and_bounded(tmp_path, identifier):
    with pytest.raises(ValueError, match="run_id"):
        LocalEvidenceStore(tmp_path, run_id=identifier)


def test_view_lookup_cannot_cross_reactivation_after_its_check(tmp_path):
    class Factory:
        @contextmanager
        def open(self, context, dependencies, config):
            yield object()

    store = LocalEvidenceStore(tmp_path)
    owner = RuntimeSession(
        plan=RuntimePlan((ComponentSpec("device", "fake"),)),
        factories={"fake": Factory()},
        evidence=store,
    )
    old = owner.__enter__()
    check = old._check_usable

    def interleaved_check():
        check()
        owner.__exit__(None, None, None)
        owner.__enter__()

    old._check_usable = interleaved_check
    try:
        with pytest.raises(KeyError):
            old.get("device")
    finally:
        owner.__exit__(None, None, None)
        store.close()


def test_event_serialization_failure_does_not_consume_identity(tmp_path):
    store = LocalEvidenceStore(tmp_path)
    try:
        with pytest.raises(TypeError):
            store.event("invalid", metadata={1: "a", "name": "b"})
        store.event("valid")
    finally:
        store.close()
    events = [json.loads(line) for line in (store.root / "events.jsonl").read_text().splitlines()]
    assert [e["name"] for e in events] == ["run_started", "valid", "run_finished"]
    assert [e["sequence"] for e in events] == [1, 2, 3]


@pytest.mark.parametrize("suffix", [".a:b", "." + "a" * 33, ".bad?", ".bad."])
def test_artifact_suffix_is_portable(tmp_path, suffix):
    store = LocalEvidenceStore(tmp_path)
    try:
        with pytest.raises(ValueError, match="portable"):
            with store.reserve_artifact(kind="log", source="fake", suffix=suffix):
                pytest.fail("invalid suffix accepted")
    finally:
        store.close()


@pytest.mark.parametrize(
    "member_name", ["package/file.py", "../escape", "/absolute", "C:escape", "link"]
)
def test_install_verifier_extracts_only_bounded_regular_files(tmp_path, member_name):
    import io
    import runpy
    import tarfile
    from pathlib import Path

    extract = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts" / "verify_install.py")
    )["extract_source"]
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        member = tarfile.TarInfo(member_name)
        member.size = 2
        if member_name == "link":
            member.type = tarfile.SYMTYPE
            member.linkname = "../escape"
        archive.addfile(member, io.BytesIO(b"ok"))
    buffer.seek(0)
    with tarfile.open(fileobj=buffer) as archive:
        if member_name == "package/file.py":
            extract(archive, tmp_path / "source")
            assert (tmp_path / "source" / member_name).read_bytes() == b"ok"
        else:
            with pytest.raises(ValueError, match="Unsafe"):
                extract(archive, tmp_path / "source")
            assert not (tmp_path / "source").exists()


@pytest.mark.parametrize("label", ["CON", "CON.log", "COM1.trace"])
def test_reserved_windows_artifact_scope_names_are_portable(tmp_path, label):
    from pathlib import PureWindowsPath

    store = LocalEvidenceStore(tmp_path)
    try:
        with store.scope(test_id=label):
            with store.reserve_artifact(kind="log", source="fake") as artifact:
                artifact.path.write_text("ok")
        relative = store.artifacts()[0].path.relative_to(store.root)
        assert all(not PureWindowsPath(part).is_reserved() for part in relative.parts)
    finally:
        store.close()
