"""Debugger contract: provider-neutral control, inspection and breakpoints.

A debugger (a hardware probe, a GDB server, a vendor tool) implements
:class:`DebuggerDriver` as a provider of :data:`DEBUGGER`. The kernel defines
only this neutral shape; nothing here names a vendor or project.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

from autospan.api.provider import Contract


@dataclass(frozen=True, slots=True)
class DebuggerVariableDump:
    """Provider-neutral rendered debugger variable/expression dump."""

    expression: str
    text: str

    @property
    def line_count(self) -> int:
        return len(self.text.splitlines())

    @property
    def byte_count(self) -> int:
        return len(self.text.encode("utf-8"))


@dataclass(frozen=True, slots=True)
class DebuggerValueChange:
    """One value observed before and after a debugger snapshot."""

    before: object
    after: object


@dataclass(frozen=True, slots=True)
class DebuggerSnapshotDiff:
    """Differences between two :class:`DebuggerSnapshot` values."""

    state: DebuggerValueChange | None = None
    variables: dict[str, DebuggerValueChange] = field(default_factory=dict)
    registers: dict[str, DebuggerValueChange] = field(default_factory=dict)
    symbols: dict[str, DebuggerValueChange] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return bool(self.state or self.variables or self.registers or self.symbols)


@dataclass(frozen=True, slots=True)
class DebuggerSnapshot:
    """Focused, provider-neutral debugger observation."""

    state: str
    variables: dict[str, object] = field(default_factory=dict)
    registers: dict[str, object] = field(default_factory=dict)
    symbols: dict[str, bytes] = field(default_factory=dict)

    def diff(self, newer: "DebuggerSnapshot") -> DebuggerSnapshotDiff:
        """Compare state and entries, using ``"<missing>"`` for absent keys."""
        return DebuggerSnapshotDiff(
            state=(
                DebuggerValueChange(self.state, newer.state) if self.state != newer.state else None
            ),
            variables=_mapping_diff(self.variables, newer.variables),
            registers=_mapping_diff(self.registers, newer.registers),
            symbols=_mapping_diff(self.symbols, newer.symbols),
        )

    def to_dict(self) -> dict[str, object]:
        """Return JSON-compatible data, rendering byte values as hexadecimal."""
        return {
            "state": self.state,
            "variables": {name: _json_value(value) for name, value in self.variables.items()},
            "registers": {name: _json_value(value) for name, value in self.registers.items()},
            "symbols": {name: value.hex() for name, value in self.symbols.items()},
        }


def _mapping_diff(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> dict[str, DebuggerValueChange]:
    missing = object()
    result: dict[str, DebuggerValueChange] = {}
    for name in sorted(before.keys() | after.keys()):
        old = before.get(name, missing)
        new = after.get(name, missing)
        if old != new:
            result[name] = DebuggerValueChange(
                "<missing>" if old is missing else old,
                "<missing>" if new is missing else new,
            )
    return result


def _json_value(value: object) -> object:
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, bytearray):
        return bytes(value).hex()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


@runtime_checkable
class DebuggerBreakpointDriver(Protocol):
    """Provider-side breakpoint handle consumed by the project facade.

    ``location`` identifies the breakpoint; ``active`` reports its status.
    ``wait`` waits for a hit using timeout/poll values in seconds; the provider
    defines failure behavior. ``remove`` removes the breakpoint. The bundled
    fake does not simulate hits: its ``wait`` returns immediately.
    """

    @property
    def location(self) -> str: ...

    @property
    def active(self) -> bool: ...

    def wait(self, timeout_s: float = 5.0, poll_interval_s: float = 0.05) -> None: ...

    def remove(self) -> None: ...


@runtime_checkable
class DebuggerDriver(Protocol):
    """Provider-owned target control, inspection and debugger commands.

    Use instances only within their owning component scope. Control operations
    (attach, prepare, run, halt, reset and system_up), expressions, commands and
    scripts use the provider's target semantics and may change target state.
    Providers must document their prerequisites and driver exceptions.

    Wait timeouts and polling intervals are seconds. ``wait_until`` repeatedly
    calls ``reader`` and returns the value satisfying ``predicate``; the named
    wait helpers observe running/stopped state or an expected expression/value.
    Memory and symbol lengths are byte counts. ``snapshot`` selects named
    variables/registers and maps each requested symbol to its byte count; the
    protocol does not guarantee an atomic observation. Timeout enforcement and
    supported expressions, registers and script options belong to the provider.
    """

    def attach(self) -> None: ...
    def prepare(self) -> None: ...
    def run(self) -> None: ...
    def halt(self) -> None: ...
    def reset(self) -> None: ...
    def system_up(self) -> None: ...
    def execute(self, command: str) -> None: ...
    def evaluate(self, expression: str) -> object: ...
    def state(self) -> str: ...
    def is_running(self) -> bool: ...
    def wait_running(self, timeout_s: float = 5.0, poll_interval_s: float = 0.05) -> None: ...
    def wait_stopped(self, timeout_s: float = 5.0, poll_interval_s: float = 0.05) -> None: ...
    def wait_until(
        self,
        reader: Any,
        predicate: Any,
        *,
        timeout_s: float = 5.0,
        poll_interval_s: float = 0.05,
        description: str = "condition",
    ) -> Any: ...
    def wait_expression(
        self,
        expression: str,
        expected: object,
        timeout_s: float = 5.0,
        poll_interval_s: float = 0.05,
    ) -> object: ...
    def wait_variable(
        self,
        name: str,
        expected: object,
        timeout_s: float = 5.0,
        poll_interval_s: float = 0.05,
    ) -> object: ...
    def read_variable(self, name: str) -> object: ...
    def write_variable(self, name: str, value: object) -> None: ...
    def dump_variable(
        self,
        expression: str,
        *,
        output_path: str | Path | None = None,
        expand: bool = True,
        multiline: bool = True,
    ) -> DebuggerVariableDump: ...
    def address_of(self, symbol: str) -> int: ...
    def read_memory_array(self, address: int, length: int) -> bytes: ...
    def read_symbol_array(self, symbol: str, length: int) -> bytes: ...
    def snapshot(
        self,
        *,
        variables: tuple[str, ...] = (),
        registers: tuple[str, ...] = (),
        symbols: Mapping[str, int] | None = None,
    ) -> DebuggerSnapshot: ...
    def set_breakpoint(
        self, location: str, *args: Any, **kwargs: Any
    ) -> DebuggerBreakpointDriver: ...
    def remove_breakpoint(self, location: str) -> None: ...
    def clear_breakpoints(self) -> None: ...
    def read_register(self, name: str) -> object: ...
    def write_register(self, name: str, value: int) -> None: ...
    def load_symbols(self, file: str | Path, *, no_code: bool = True) -> None: ...
    def run_script(
        self, file: str | Path, *arguments: object, timeout_s: float | None = None
    ) -> None: ...


DEBUGGER = Contract("debugger", 1, DebuggerDriver)
