"""Plugin-contributed frontends (``autospan run NAME``).

Frontends are presentation adapters such as a rich operator console or a
bench doctor. They receive the compiled plan and may open the one canonical
runtime through :meth:`FrontendContext.open_runtime`; they must never build a
second device/backend lifecycle of their own.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from autospan.api.commands import CommandContribution
from autospan.api.component import RuntimePlan
from autospan.api.runtime import Runtime

_FRONTEND_NAME = re.compile(r"^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)*$")


class FrontendContext(Protocol):
    """Host services visible to a contributed frontend."""

    @property
    def plan(self) -> RuntimePlan:
        """Compiled, validated, side-effect-free runtime plan."""
        ...

    @property
    def commands(self) -> tuple[CommandContribution, ...]:
        """Console commands contributed by the selected plugins."""
        ...

    @property
    def config(self) -> Mapping[str, Any]:
        """Effective host configuration (after ``--set`` overrides)."""
        ...

    @property
    def config_path(self) -> Path | None:
        """Selected AutoSpan configuration file, when the host was built from one."""
        ...

    @property
    def evidence_root(self) -> Path:
        """Directory under which a runtime opened by this frontend stores evidence."""
        ...

    @property
    def enabled_gates(self) -> frozenset[str]:
        """Gates the operator enabled for this frontend invocation."""
        ...

    def open_runtime(self) -> AbstractContextManager[Runtime]:
        """Acquire the canonical runtime (and its evidence run) for this frontend."""
        ...

    def run_tests(self, pytest_args: tuple[str, ...] = ()) -> int:
        """Run the selected plugins' scenarios exactly like ``autospan test``."""
        ...


FrontendHandler = Callable[[FrontendContext, tuple[str, ...]], int | None]


@dataclass(frozen=True, slots=True)
class FrontendContribution:
    """One named frontend exposed as ``autospan run NAME``."""

    name: str
    handler: FrontendHandler
    help: str = ""

    def __post_init__(self) -> None:
        if not _FRONTEND_NAME.match(self.name):
            raise ValueError(
                f"Frontend name {self.name!r} must be dotted lowercase tokens, e.g. 'myproject.console'"
            )
