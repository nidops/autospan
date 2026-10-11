"""Host-side implementation of :class:`autospan.api.FrontendContext`."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from autospan.api.commands import CommandContribution
from autospan.api.component import RuntimePlan
from autospan.api.runtime import Runtime
from autospan._core.evidence_store import LocalEvidenceStore
from autospan._core.host import HostBuild
from autospan._core.runtime import RuntimeSession


class HostFrontendContext:
    """Services handed to plugin frontends; one runtime model, no parallel lifecycle."""

    def __init__(
        self,
        host: HostBuild,
        *,
        evidence_root: Path,
        enabled_gates: frozenset[str] = frozenset(),
        frontend_name: str = "frontend",
    ) -> None:
        self._host = host
        self._evidence_root = evidence_root
        self._enabled_gates = enabled_gates
        self._frontend_name = frontend_name

    @property
    def plan(self) -> RuntimePlan:
        return self._host.plan

    @property
    def commands(self) -> tuple[CommandContribution, ...]:
        return self._host.commands

    @property
    def config(self) -> Mapping[str, Any]:
        return self._host.config

    @property
    def config_path(self) -> Path | None:
        return self._host.config_path

    @property
    def evidence_root(self) -> Path:
        return self._evidence_root

    @property
    def enabled_gates(self) -> frozenset[str]:
        return self._enabled_gates

    @contextmanager
    def open_runtime(self) -> Iterator[Runtime]:
        evidence = LocalEvidenceStore(self._evidence_root)
        outcome = "failed"
        try:
            session = RuntimeSession(
                plan=self._host.plan,
                factories=self._host.factories,
                evidence=evidence,
                enabled_gates=self._enabled_gates,
            )
            with session as runtime:
                yield runtime
                session.set_outcome("passed")
            outcome = "passed"
        finally:
            evidence.close(outcome=outcome, metadata={"frontend": self._frontend_name})

    def run_tests(self, pytest_args: tuple[str, ...] = ()) -> int:
        from autospan.frontends.test_command import run_tests

        return run_tests(
            host=self._host,
            evidence_root=self._evidence_root,
            pytest_args=tuple(pytest_args),
        )
