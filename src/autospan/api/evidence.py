"""Protocol-neutral evidence contracts."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol


@dataclass(frozen=True, slots=True)
class EvidenceContext:
    """Correlation identifiers attached to events and artifacts."""

    run_id: str
    test_id: str | None = None
    step_id: str | None = None
    source: str = "autospan"


@dataclass(slots=True)
class StepVerdict:
    """Optional explicit verdict for work that reports failure without raising.

    Set the status/error before the step exits. Existing exception-based steps
    keep their original behavior and still yield an EvidenceContext.
    """

    status: int = 0
    error: str | None = None

    @property
    def outcome(self) -> str:
        return "failed" if self.status != 0 or self.error is not None else "passed"


@dataclass(frozen=True, slots=True)
class Artifact:
    """Committed evidence artifact metadata."""

    artifact_id: str
    path: Path
    kind: str
    source: str
    media_type: str
    format: str | None
    complete: bool
    size: int
    sha256: str
    context: EvidenceContext
    attributes: Mapping[str, Any] = field(default_factory=dict)


class ArtifactReservation(Protocol):
    """Temporary artifact path; close every writer before its context exits.

    Exit commits an opaque file with size and SHA-256 metadata. A producer
    exception commits the available bytes with ``complete=False``.
    """

    @property
    def path(self) -> Path:
        """Writable temporary path owned by the reservation until context exit."""
        ...


class Evidence(Protocol):
    """Small public surface components/scenarios use to publish evidence.

    Worker threads should explicitly capture ``current_context`` on the owning
    thread and re-bind it with ``bind_context`` inside the worker when test/step
    correlation is required. Python ``contextvars`` do not implicitly propagate
    to newly created threads.
    """

    @property
    def current_context(self) -> EvidenceContext:
        """Return the current run/test/step correlation context."""
        ...

    def event(self, name: str, *, source: str = "autospan", **attributes: Any) -> None:
        """Append one low-rate structured control-plane event."""
        ...

    def scope(
        self,
        *,
        test_id: str | None = None,
        step_id: str | None = None,
        source: str = "autospan",
    ) -> AbstractContextManager[EvidenceContext]:
        """Temporarily override test/step/source correlation fields."""
        ...

    def bind_context(self, context: EvidenceContext) -> AbstractContextManager[EvidenceContext]:
        """Bind a previously captured context, typically inside a worker thread."""
        ...

    def step(
        self, name: str, *, source: str = "autospan", verdict: StepVerdict | None = None
    ) -> AbstractContextManager[EvidenceContext]:
        """Create a named human-readable evidence step and correlate nested output."""
        ...

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
    ) -> AbstractContextManager[ArtifactReservation]:
        """Reserve a path and commit after all writers finish, even on producer failure.

        Close/join writers inside the block. I/O or finalization errors propagate
        to the caller; a reservation must not outlive its evidence run.
        """
        ...

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
        """Copy an existing file into the current evidence scope as one artifact."""
        ...
