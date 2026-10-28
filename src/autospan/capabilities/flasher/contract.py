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
    """Provider-reported success or failure of a flash operation."""

    OK = "ok"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class FlashRequest:
    """Inputs for one provider-controlled flash operation.

    ``artifact`` is an optional input path; its format and ``parameters`` are
    provider-defined. ``timeout_s`` is the requested timeout in seconds, default
    600; timeout enforcement belongs to the provider. This record performs no input
    validation or I/O and retains the supplied parameters mapping without copying.
    """

    artifact: Path | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)
    timeout_s: float = 600.0


@dataclass(frozen=True, slots=True)
class FlashResult:
    """Provider outcome with optional tool diagnostics and a report path.

    Pass a ``FlashStatus`` member for ``status``. ``ok`` examines only that status;
    it does not reconcile ``return_code`` or ``error_type``. Successful providers
    should return code zero and no error type, as checked by ``check_flasher``.
    ``report_path`` references provider output; this record does not copy it or
    attach it to evidence.
    """

    status: FlashStatus
    message: str = ""
    return_code: int = 0
    stdout: str = ""
    stderr: str = ""
    report_path: Path | None = None
    error_type: str | None = None

    @property
    def ok(self) -> bool:
        """Whether the provider reported ``FlashStatus.OK``."""
        return self.status is FlashStatus.OK


@runtime_checkable
class Flasher(Protocol):
    """Flash tool interface; its component factory owns acquisition and cleanup."""

    def flash(self, request: FlashRequest) -> FlashResult:
        """Flash the target. Tool failures are reported in the result, not raised."""
        ...


FLASHER = Contract("flasher", 1, Flasher)
