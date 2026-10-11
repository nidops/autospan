"""Local filesystem evidence store with opaque artifacts and correlated events."""

from __future__ import annotations

import contextvars
import hashlib
import json
import os
import shutil
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from threading import RLock
from typing import Any, Iterator, Mapping

from autospan.api.evidence import Artifact, EvidenceContext, StepVerdict


@dataclass(slots=True)
class _Reservation:
    path: Path
    final_path: Path
    artifact_id: str
    kind: str
    source: str
    media_type: str
    format: str | None
    attributes: Mapping[str, Any]
    context: EvidenceContext
    artifact: Artifact | None = None


class LocalEvidenceStore:
    """Run-scoped evidence custodian.

    Bulk traffic bytes remain in protocol/vendor recorders and arrive here as
    opaque artifacts. Event/file bookkeeping is protected by an ``RLock`` so
    low-rate control-plane evidence may safely be emitted by worker threads.
    """

    def __init__(self, root: Path, *, run_id: str | None = None) -> None:
        self.run_id = _make_run_id() if run_id is None else run_id
        if (
            not isinstance(self.run_id, str)
            or self.run_id in {".", ".."}
            or not self.run_id
            or "/" in self.run_id
            or "\\" in self.run_id
            or PureWindowsPath(self.run_id).drive
            or _safe_token(self.run_id) != self.run_id
        ):
            raise ValueError("run_id must be a single non-empty directory name")
        self.root = root / self.run_id
        self.root.mkdir(parents=True, exist_ok=False)
        self._events_path = self.root / "events.jsonl"
        self._manifest_path = self.root / "manifest.json"
        self._events_file = self._events_path.open("a", encoding="utf-8")
        self._artifacts: list[Artifact] = []
        self._reserved_paths: set[Path] = set()
        self._lock = RLock()
        self._closed = False
        self._write_error: BaseException | None = None
        self._finished = False
        self._pending_manifest: str | None = None
        self._step_sequence = 0
        self._event_sequence = 0
        self._context: contextvars.ContextVar[EvidenceContext] = contextvars.ContextVar(
            "autospan_evidence_context",
            default=EvidenceContext(run_id=self.run_id),
        )
        self.event("run_started")

    @property
    def current_context(self) -> EvidenceContext:
        """Return the evidence context bound to the current execution context."""
        return self._context.get()

    @contextmanager
    def scope(
        self,
        *,
        test_id: str | None = None,
        step_id: str | None = None,
        source: str = "autospan",
    ) -> Iterator[EvidenceContext]:
        """Temporarily derive and bind a test/step evidence context."""
        parent = self.current_context
        same_step = (test_id is None or test_id == parent.test_id) and (
            step_id is None or step_id == parent.step_id
        )
        context = EvidenceContext(
            run_id=self.run_id,
            test_id=test_id if test_id is not None else parent.test_id,
            step_id=step_id if step_id is not None else parent.step_id,
            source=source,
            step_uid=parent.step_uid if same_step else None,
            parent_step_uid=parent.parent_step_uid if same_step else None,
            step_sequence=parent.step_sequence if same_step else None,
        )
        with self.bind_context(context):
            yield context

    @contextmanager
    def bind_context(self, context: EvidenceContext) -> Iterator[EvidenceContext]:
        """Bind an explicit context, useful when a worker thread must inherit correlation."""
        if context.run_id != self.run_id:
            raise ValueError(
                f"Evidence context run {context.run_id!r} does not belong to {self.run_id!r}"
            )
        token = self._context.set(context)
        try:
            yield context
        finally:
            self._context.reset(token)

    @contextmanager
    def step(
        self, name: str, *, source: str = "autospan", verdict: StepVerdict | None = None
    ) -> Iterator[EvidenceContext]:
        """Create a human-readable evidence step nested under the current test."""
        label = str(name).strip()
        if not label:
            raise ValueError("Evidence step name must not be empty")
        parent = self.current_context
        with self._lock:
            if self._closed or self._finished:
                raise RuntimeError("Evidence store is closed")
            self._step_sequence += 1
            sequence = self._step_sequence
        context = EvidenceContext(
            run_id=self.run_id,
            test_id=parent.test_id,
            step_id=label,
            source=source,
            step_uid=f"step-{sequence:08d}",
            parent_step_uid=parent.step_uid,
            step_sequence=sequence,
        )
        started = time.monotonic()
        with self.bind_context(context):
            self.event("step_started", source=source, step=label)
            try:
                yield context
            except BaseException as exc:
                try:
                    self.event(
                        "step_finished",
                        source=source,
                        step=label,
                        outcome="failed",
                        duration_s=time.monotonic() - started,
                        error=repr(exc),
                    )
                except BaseException as reporting_error:
                    exc.add_note(f"Step finalization failed: {reporting_error!r}")
                raise
            else:
                attributes: dict[str, Any] = {}
                if verdict is not None:
                    attributes["status"] = verdict.status
                    if verdict.error is not None:
                        attributes["error"] = verdict.error
                self.event(
                    "step_finished",
                    source=source,
                    step=label,
                    outcome=verdict.outcome if verdict is not None else "passed",
                    duration_s=time.monotonic() - started,
                    **attributes,
                )

    def event(self, name: str, *, source: str = "autospan", **attributes: Any) -> None:
        """Append one structured low-rate control-plane event."""
        context = self.current_context
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "schema_version": 2,
            "name": name,
            "wall_time": now,
            "monotonic_ns": time.monotonic_ns(),
            "run_id": context.run_id,
            "test_id": context.test_id,
            "step_id": context.step_id,
            "step_uid": context.step_uid,
            "parent_step_uid": context.parent_step_uid,
            "step_sequence": context.step_sequence,
            "source": source,
            "attributes": attributes,
        }
        with self._lock:
            if self._closed or self._finished:
                raise RuntimeError("Evidence store is closed")
            payload["sequence"] = self._event_sequence + 1
            serialized = json.dumps(payload, sort_keys=True, default=str) + "\n"
            position = self._events_file.tell()
            try:
                self._events_file.write(serialized)
                self._events_file.flush()
            except BaseException as exc:
                try:
                    self._events_file.seek(position)
                    self._events_file.truncate()
                except BaseException as rollback_error:
                    self._write_error = rollback_error
                    self._closed = True
                    exc.add_note(f"Event rollback failed; store is unusable: {rollback_error!r}")
                raise
            self._event_sequence += 1

    @contextmanager
    def reserve_artifact(
        self,
        *,
        kind: str,
        source: str,
        suffix: str = "",
        media_type: str = "application/octet-stream",
        format: str | None = None,
        attributes: Mapping[str, Any] | None = None,
        name: str | None = None,
    ) -> Iterator[_Reservation]:
        """Reserve a temporary file and atomically commit it on scope exit."""
        artifact_id = uuid.uuid4().hex
        context = self.current_context
        suffix = _validated_suffix(suffix)
        kind = _required_label(kind, "kind")
        source = _required_label(source, "source")
        media_type = _required_label(media_type, "media_type")
        if format is not None:
            format = _required_label(format, "format")
        scope_dir = self._scope_dir(context)
        scope_dir.mkdir(parents=True, exist_ok=True)
        safe_source = _safe_token(source)
        readable_name = _safe_token(name) if name else artifact_id
        # Name allocation is guarded independently from file creation. Without
        # this reservation set, two worker threads can both observe that the
        # same readable name does not exist yet and select one partial path.
        with self._lock:
            if self._closed or self._finished:
                raise RuntimeError("Evidence store is closed")
            final_name = f"{safe_source}-{readable_name}{suffix}"
            final_path = scope_dir / final_name
            if name and (
                final_path.exists()
                or final_path in self._reserved_paths
                or any(item.path == final_path for item in self._artifacts)
            ):
                final_name = f"{safe_source}-{readable_name}-{artifact_id[:8]}{suffix}"
                final_path = scope_dir / final_name
                while final_path in self._reserved_paths or final_path.exists():
                    artifact_id = uuid.uuid4().hex
                    final_name = f"{safe_source}-{readable_name}-{artifact_id[:8]}{suffix}"
                    final_path = scope_dir / final_name
            self._reserved_paths.add(final_path)
        temp_path = scope_dir / f".{final_name}.{artifact_id[:8]}.partial"
        reservation = _Reservation(
            path=temp_path,
            final_path=final_path,
            artifact_id=artifact_id,
            kind=kind,
            source=source,
            media_type=media_type,
            format=format,
            attributes=dict(attributes or {}),
            context=context,
        )
        error: BaseException | None = None
        try:
            yield reservation
        except BaseException as exc:
            error = exc
            raise
        finally:
            try:
                if temp_path.exists():
                    os.replace(temp_path, final_path)
                    reservation.artifact = self._commit_reservation(
                        reservation, complete=error is None
                    )
                else:
                    self.event(
                        "artifact_missing",
                        source=source,
                        artifact_id=artifact_id,
                        kind=kind,
                    )
            except BaseException as reporting_error:
                if error is None:
                    raise
                error.add_note(f"Artifact finalization failed: {reporting_error!r}")
            finally:
                with self._lock:
                    self._reserved_paths.discard(final_path)

    def attach_file(
        self,
        source_path: Path,
        *,
        kind: str,
        source: str,
        media_type: str = "application/octet-stream",
        format: str | None = None,
        attributes: Mapping[str, Any] | None = None,
        name: str | None = None,
    ) -> Artifact:
        """Copy an existing file into the current evidence scope and return its metadata."""
        source_path = Path(source_path)
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        suffix = "".join(source_path.suffixes)
        with self.reserve_artifact(
            kind=kind,
            source=source,
            suffix=suffix,
            media_type=media_type,
            format=format,
            attributes=attributes,
            name=name,
        ) as reservation:
            shutil.copy2(source_path, reservation.path)
        if reservation.artifact is None:
            raise RuntimeError("Evidence artifact commit did not produce an artifact")
        return reservation.artifact

    def close(self, *, outcome: str = "unknown", metadata: Mapping[str, Any] | None = None) -> None:
        """Finalize events and atomically publish the run manifest."""
        with self._lock:
            if self._write_error is not None:
                raise RuntimeError(
                    "Evidence event stream could not be repaired"
                ) from self._write_error
            if self._closed:
                return
            if self._reserved_paths:
                raise RuntimeError(
                    "Evidence store cannot close with active artifact reservation(s): "
                    f"{len(self._reserved_paths)}"
                )
            if self._pending_manifest is None:
                manifest = {
                    "schema_version": 2,
                    "run_id": self.run_id,
                    "outcome": outcome,
                    "metadata": dict(metadata or {}),
                    "artifacts": [_artifact_to_json(item, self.root) for item in self._artifacts],
                }
                # Invalid metadata leaves the run open. Once staged, retries publish
                # the same verdict and snapshot, even if a caller supplies new values.
                serialized = json.dumps(manifest, indent=2, sort_keys=True, default=str)
            else:
                serialized = self._pending_manifest
            temp = self._manifest_path.with_suffix(".json.tmp")
            temp.write_text(serialized, encoding="utf-8")
            if not self._finished:
                self.event("run_finished", outcome=json.loads(serialized)["outcome"])
                self._finished = True
                self._pending_manifest = serialized
            self._events_file.close()
            os.replace(temp, self._manifest_path)
            self._closed = True

    def artifacts(self) -> tuple[Artifact, ...]:
        """Return a stable snapshot of committed artifacts."""
        with self._lock:
            return tuple(self._artifacts)

    def _scope_dir(self, context: EvidenceContext) -> Path:
        test_dir = (
            self.root / "session"
            if context.test_id is None
            else self.root / "tests" / _safe_token(context.test_id)
        )
        identity = context.step_uid or context.step_id
        if identity is None:
            return test_dir
        return test_dir / "steps" / _safe_token(identity)

    def _commit_reservation(self, reservation: _Reservation, *, complete: bool) -> Artifact:
        digest = hashlib.sha256()
        with reservation.final_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        artifact = Artifact(
            artifact_id=reservation.artifact_id,
            path=reservation.final_path,
            kind=reservation.kind,
            source=reservation.source,
            media_type=reservation.media_type,
            format=reservation.format,
            complete=complete,
            size=reservation.final_path.stat().st_size,
            sha256=digest.hexdigest(),
            context=reservation.context,
            attributes=reservation.attributes,
        )
        with self._lock:
            self._artifacts.append(artifact)
        # Bind the artifact's original context while recording commit metadata;
        # this matters when a worker thread commits an artifact after capturing
        # a test context explicitly.
        with self.bind_context(reservation.context):
            self.event(
                "artifact_committed",
                source=reservation.source,
                artifact_id=artifact.artifact_id,
                kind=artifact.kind,
                format=artifact.format,
                complete=artifact.complete,
                size=artifact.size,
                sha256=artifact.sha256,
            )
        return artifact


def _required_label(value: str, field: str) -> str:
    label = str(value).strip()
    if not label:
        raise ValueError(f"Evidence artifact {field} must not be empty")
    return label


def _validated_suffix(value: str) -> str:
    suffix = str(value)
    if not suffix:
        return ""
    if "/" in suffix or "\\" in suffix:
        raise ValueError("Evidence artifact suffix must not contain path separators")
    if not suffix.startswith("."):
        raise ValueError("Evidence artifact suffix must be empty or start with '.'")
    if (
        suffix.endswith(".")
        or len(suffix) > 32
        or any(not (ch.isascii() and (ch.isalnum() or ch in "._-")) for ch in suffix)
    ):
        raise ValueError(
            "Evidence artifact suffix must be a portable extension of at most 32 characters"
        )
    if suffix in {".", ".."}:
        raise ValueError("Evidence artifact suffix must include a file extension")
    return suffix


def _artifact_to_json(artifact: Artifact, root: Path) -> dict[str, Any]:
    payload = asdict(artifact)
    payload["path"] = artifact.path.relative_to(root).as_posix()
    payload["context"] = asdict(artifact.context)
    return payload


def _make_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def _safe_token(value: str) -> str:
    chars = [ch if ch.isascii() and (ch.isalnum() or ch in "-_.") else "_" for ch in value]
    token = "".join(chars).strip("._")
    # Hash altered/truncated names to avoid both collisions and UTF-8 byte limits.
    reserved = token.split(".", 1)[0].upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CONIN$",
        "CONOUT$",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    if reserved:
        token = "item-" + token
    if token != value or len(token) > 80 or not token:
        return (token[:80] or "unnamed") + "-" + hashlib.sha256(value.encode()).hexdigest()[:16]
    return token
