"""Discard simulated events; never create project evidence or artifact files."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path
from typing import Any, NoReturn

from autospan.api import EvidenceContext, StepVerdict


class SimulationEvidence:
    """Evidence contract for the isolated Lab; only UI log exports use ``root``."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._context: ContextVar[EvidenceContext] = ContextVar(
            "lab_evidence", default=EvidenceContext(run_id="simulation", source="lab")
        )

    @property
    def current_context(self) -> EvidenceContext:
        return self._context.get()

    def event(self, name: str, *, source: str = "autospan", **attributes: Any) -> None:
        pass

    @contextmanager
    def bind_context(self, context: EvidenceContext) -> Iterator[EvidenceContext]:
        token = self._context.set(context)
        try:
            yield context
        finally:
            self._context.reset(token)

    @contextmanager
    def scope(
        self, *, test_id: str | None = None, step_id: str | None = None, source: str = "autospan"
    ) -> Iterator[EvidenceContext]:
        current = self.current_context
        context = replace(
            current,
            test_id=test_id if test_id is not None else current.test_id,
            step_id=step_id if step_id is not None else current.step_id,
            source=source,
        )
        with self.bind_context(context):
            yield context

    @contextmanager
    def step(
        self, name: str, *, source: str = "autospan", verdict: StepVerdict | None = None
    ) -> Iterator[EvidenceContext]:
        with self.scope(step_id=name, source=source) as context:
            yield context

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
    ) -> NoReturn:
        raise NotImplementedError("The simulated Lab does not record artifacts")

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
    ) -> NoReturn:
        raise NotImplementedError("The simulated Lab does not record artifacts")
