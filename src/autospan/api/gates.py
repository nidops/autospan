"""Execution-gate contribution contract.

A gate is an explicit operator opt-in such as ``--hardware``. Scenarios opt in
through a pytest marker of the same name; runtime components opt in through
``ComponentSpec.gates``. A gate may imply other gates: a ``destructive`` test
is also a ``hardware`` test, so enabling it alone is not enough.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_GATE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class GateContribution:
    """One named execution gate exposed as ``--<name>`` and marker ``<name>``."""

    name: str
    help: str = ""
    implies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _GATE_NAME.fullmatch(self.name):
            raise ValueError(
                f"Gate name {self.name!r} must be a lowercase identifier (it becomes a pytest marker)"
            )
        object.__setattr__(self, "implies", tuple(dict.fromkeys(self.implies)))
        if self.name in self.implies:
            raise ValueError(f"Gate {self.name!r} cannot imply itself")

    @property
    def option(self) -> str:
        """Command-line flag enabling this gate."""
        return "--" + self.name.replace("_", "-")
