"""Small plugin-author test harnesses."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from autospan.api.component import ComponentFactory, ComponentSpec, RuntimePlan
from autospan._core.evidence_store import LocalEvidenceStore
from autospan._core.runtime import RuntimeSession


class FactoryHarness:
    """Open a tiny runtime for factory tests using real filesystem evidence.

    The caller owns the returned session and must close ``session.evidence``
    after exiting it, including when acquisition fails.
    """

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path

    def open(
        self,
        *,
        components: tuple[ComponentSpec, ...],
        factories: Mapping[str, ComponentFactory],
    ) -> RuntimeSession:
        """Return an unentered session; this helper does not validate a host plan."""
        evidence = LocalEvidenceStore(self.tmp_path / "evidence")
        # Caller owns the returned session. Evidence is deliberately reachable
        # through runtime.evidence and may be finalised by the test as needed.
        return RuntimeSession(
            plan=RuntimePlan(components=components),
            factories=factories,
            evidence=evidence,
        )
