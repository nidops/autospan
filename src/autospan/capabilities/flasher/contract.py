"""Flasher contract: flash a target from a request and report the outcome.

The kernel knows nothing about *how* a target is flashed. A vendor tool, a
JTAG probe, a bootloader protocol or a plain script each implement
:class:`Flasher` as a provider of :data:`FLASHER`; projects select one by name.

A request carries an optional artifact path plus free-form ``parameters``
(project alias, version, case, ...) whose meaning belongs to the provider and
the project plugin, never to the kernel.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from autospan.api.provider import Contract


class FlashStatus(str, Enum):
    OK = "ok"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class FlashRequest:
    artifact: Path | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)
    timeout_s: float = 600.0


@dataclass(frozen=True, slots=True)
class FlashResult:
    status: FlashStatus
    message: str = ""
    return_code: int = 0
    stdout: str = ""
    stderr: str = ""
    report_path: Path | None = None
    error_type: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is FlashStatus.OK


@runtime_checkable
class Flasher(Protocol):
    def flash(self, request: FlashRequest) -> FlashResult:
        """Flash the target. Tool failures are reported in the result, not raised."""
        ...


FLASHER = Contract("flasher", 1, Flasher)
