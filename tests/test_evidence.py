import json
import threading
from pathlib import Path

from autospan.api import Artifact
from autospan._core.evidence_store import LocalEvidenceStore


def test_evidence_accepts_arbitrary_artifact_formats(tmp_path):
    store = LocalEvidenceStore(tmp_path / "evidence", run_id="run")
    try:
        with store.scope(test_id="test_anything"):
            for suffix, fmt, payload in (
                (".blf", "blf", b"CAN"),
                (".pcapng", "pcapng", b"BLE"),
                (".vendor", "mystery", b"???"),
            ):
                with store.reserve_artifact(
                    kind="traffic",
                    source="capture",
                    suffix=suffix,
                    format=fmt,
                ) as reservation:
                    reservation.path.write_bytes(payload)
    finally:
        store.close(outcome="passed")

    manifest = json.loads((tmp_path / "evidence" / "run" / "manifest.json").read_text())
    assert [item["format"] for item in manifest["artifacts"]] == ["blf", "pcapng", "mystery"]
    assert all(item["sha256"] for item in manifest["artifacts"])


def test_failed_artifact_is_preserved_as_incomplete(tmp_path):
    store = LocalEvidenceStore(tmp_path / "evidence", run_id="run")
    try:
        try:
            with store.reserve_artifact(kind="debug", source="gdb", suffix=".log") as reservation:
                reservation.path.write_text("partial", encoding="utf-8")
                raise RuntimeError("capture failed")
        except RuntimeError:
            pass
    finally:
        store.close(outcome="failed")

    manifest = json.loads((tmp_path / "evidence" / "run" / "manifest.json").read_text())
    assert manifest["artifacts"][0]["complete"] is False


def test_native_evidence_keeps_protocol_neutral_metadata(tmp_path):
    store = LocalEvidenceStore(tmp_path / "evidence", run_id="run")
    try:
        with store.scope(test_id="scenario::capture"):
            cases = (
                (".blf", "blf", "application/octet-stream", b"can"),
                (".pcapng", "pcapng", "application/vnd.tcpdump.pcap", b"air"),
                (".log", "text", "text/plain", b"uart tx/rx"),
                (".csv", "csv", "text/csv", b"t,current\n0,1.2\n"),
                (".bin", "raw", "application/octet-stream", b"\x00\x01"),
            )
            for suffix, fmt, media_type, payload in cases:
                with store.reserve_artifact(
                    kind="traffic",
                    source="device.capture",
                    suffix=suffix,
                    format=fmt,
                    media_type=media_type,
                    attributes={"channel": "logical-1"},
                    name="capture",
                ) as reservation:
                    reservation.path.write_bytes(payload)
    finally:
        store.close(outcome="passed")

    manifest = json.loads((tmp_path / "evidence" / "run" / "manifest.json").read_text())
    assert {item["format"] for item in manifest["artifacts"]} == {
        "blf",
        "pcapng",
        "text",
        "csv",
        "raw",
    }
    assert all(item["kind"] == "traffic" for item in manifest["artifacts"])
    assert all(item["attributes"]["channel"] == "logical-1" for item in manifest["artifacts"])
    assert all(item["context"]["test_id"] == "scenario::capture" for item in manifest["artifacts"])


def test_concurrent_readable_artifact_names_are_unique(tmp_path):
    store = LocalEvidenceStore(tmp_path / "evidence", run_id="run")
    barrier = threading.Barrier(8)
    errors = []

    def worker(index):
        try:
            with store.reserve_artifact(
                kind="traffic",
                source="uart.port",
                suffix=".log",
                format="text",
                name="exchange",
            ) as reservation:
                barrier.wait(timeout=2.0)
                reservation.path.write_text(str(index), encoding="utf-8")
        except BaseException as exc:  # assertion below reports worker failures
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3.0)
        assert not errors
        assert all(not thread.is_alive() for thread in threads)
        paths = [artifact.path for artifact in store.artifacts()]
        assert len(paths) == 8
        assert len(set(paths)) == 8
        assert {path.read_text(encoding="utf-8") for path in paths} == {str(i) for i in range(8)}
    finally:
        store.close(outcome="passed")


def test_artifact_suffix_cannot_escape_evidence_directory(tmp_path):
    import pytest

    store = LocalEvidenceStore(tmp_path / "evidence", run_id="run")
    try:
        with pytest.raises(ValueError, match="path separators"):
            with store.reserve_artifact(
                kind="traffic",
                source="capture",
                suffix="/../../escape.bin",
            ):
                pass
    finally:
        store.close(outcome="passed")


def test_store_refuses_close_with_active_artifact_reservation(tmp_path):
    store = LocalEvidenceStore(tmp_path / "evidence", run_id="run")
    manager = store.reserve_artifact(kind="traffic", source="capture", suffix=".bin", name="active")
    reservation = manager.__enter__()
    try:
        reservation.path.write_bytes(b"partial")
        import pytest

        with pytest.raises(RuntimeError, match="active artifact reservation"):
            store.close(outcome="failed")
    finally:
        manager.__exit__(None, None, None)
        store.close(outcome="passed")


def test_concurrent_attach_file_returns_its_own_artifact(tmp_path: Path) -> None:
    store = LocalEvidenceStore(tmp_path / "evidence", run_id="run")
    source_a = tmp_path / "a.log"
    source_b = tmp_path / "b.log"
    source_a.write_text("A", encoding="utf-8")
    source_b.write_text("B", encoding="utf-8")
    barrier = threading.Barrier(2)
    returned: list[Artifact] = []
    guard = threading.Lock()

    def attach(source_path: Path) -> None:
        barrier.wait()
        artifact = store.attach_file(
            source_path,
            kind="traffic",
            source="uart.device",
            name="session",
            format="text",
            media_type="text/plain",
        )
        with guard:
            returned.append(artifact)

    threads = [
        threading.Thread(target=attach, args=(source_a,)),
        threading.Thread(target=attach, args=(source_b,)),
    ]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=1.0)
            assert not thread.is_alive()
        assert len(returned) == 2
        assert len({item.artifact_id for item in returned}) == 2
        assert {item.path.read_text(encoding="utf-8") for item in returned} == {"A", "B"}
    finally:
        store.close(outcome="passed")
