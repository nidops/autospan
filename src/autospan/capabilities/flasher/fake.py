"""Hardware-free flasher provider used by simulations and contract tests."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from autospan.capabilities.flasher.contract import FlashRequest, FlashResult, FlashStatus


class FakeFlasher:
    """Records requests; fails on demand with ``outcome: failed``."""

    def __init__(self, outcome: str = "ok") -> None:
        if outcome not in ("ok", "failed"):
            raise ValueError("fake flasher outcome must be 'ok' or 'failed'")
        self._outcome = FlashStatus(outcome)
        self.requests: list[FlashRequest] = []

    def flash(self, request: FlashRequest) -> FlashResult:
        self.requests.append(request)
        if self._outcome is FlashStatus.FAILED:
            return FlashResult(FlashStatus.FAILED, "fake failure", return_code=1)
        return FlashResult(FlashStatus.OK, "fake flash")


class FakeFlasherFactory:
    @contextmanager
    def open(
        self, context: Any, dependencies: Mapping[str, object], config: Mapping[str, Any]
    ) -> Iterator[FakeFlasher]:
        del context, dependencies
        yield FakeFlasher(str(config.get("outcome", "ok")))
