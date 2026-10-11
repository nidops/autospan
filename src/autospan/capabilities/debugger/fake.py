"""Hardware-free debugger provider for simulations and contract tests."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from autospan.capabilities.debugger.contract import (
    DebuggerSnapshot,
    DebuggerVariableDump,
)


class FakeBreakpoint:
    def __init__(self, location: str, owner: "FakeDebugger") -> None:
        self.location = location
        self.active = True
        self._owner = owner

    def wait(self, timeout_s: float = 5.0, poll_interval_s: float = 0.05) -> None:
        del timeout_s, poll_interval_s

    def remove(self) -> None:
        self.active = False
        self._owner.breakpoints.pop(self.location, None)


class FakeDebugger:
    """In-memory target: variables, registers and memory are plain dictionaries."""

    def __init__(self, variables: Mapping[str, Any] | None = None) -> None:
        self.variables: dict[str, Any] = dict(variables or {})
        self.registers: dict[str, int] = {}
        self.memory: dict[int, int] = {}
        self.symbols: dict[str, int] = {}
        self.breakpoints: dict[str, FakeBreakpoint] = {}
        self._state = "stopped"
        self.commands: list[str] = []

    def attach(self) -> None:
        self._state = "stopped"

    def prepare(self) -> None:
        pass

    def run(self) -> None:
        self._state = "running"

    def halt(self) -> None:
        self._state = "stopped"

    def reset(self) -> None:
        self._state = "stopped"

    def system_up(self) -> None:
        self._state = "running"

    def execute(self, command: str) -> None:
        self.commands.append(command)

    def evaluate(self, expression: str) -> object:
        return self.variables.get(expression, 0)

    def state(self) -> str:
        return self._state

    def is_running(self) -> bool:
        return self._state == "running"

    def wait_running(self, timeout_s: float = 5.0, poll_interval_s: float = 0.05) -> None:
        self.wait_until(
            self.state,
            lambda s: s == "running",
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
            description="running",
        )

    def wait_stopped(self, timeout_s: float = 5.0, poll_interval_s: float = 0.05) -> None:
        self.wait_until(
            self.state,
            lambda s: s == "stopped",
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
            description="stopped",
        )

    def wait_until(
        self,
        reader: Callable[[], Any],
        predicate: Callable[[Any], bool],
        *,
        timeout_s: float = 5.0,
        poll_interval_s: float = 0.05,
        description: str = "condition",
    ) -> Any:
        deadline = time.monotonic() + timeout_s
        while True:
            value = reader()
            if predicate(value):
                return value
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for {description}")
            time.sleep(poll_interval_s)

    def wait_expression(
        self,
        expression: str,
        expected: object,
        timeout_s: float = 5.0,
        poll_interval_s: float = 0.05,
    ) -> object:
        return self.wait_until(
            lambda: self.evaluate(expression),
            lambda v: v == expected,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
            description=expression,
        )

    def wait_variable(
        self, name: str, expected: object, timeout_s: float = 5.0, poll_interval_s: float = 0.05
    ) -> object:
        return self.wait_until(
            lambda: self.read_variable(name),
            lambda v: v == expected,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
            description=name,
        )

    def read_variable(self, name: str) -> object:
        return self.variables[name]

    def write_variable(self, name: str, value: object) -> None:
        self.variables[name] = value

    def dump_variable(
        self,
        expression: str,
        *,
        output_path: str | Path | None = None,
        expand: bool = True,
        multiline: bool = True,
    ) -> DebuggerVariableDump:
        del expand, multiline
        text = f"{expression} = {self.variables.get(expression)!r}\n"
        if output_path is not None:
            Path(output_path).write_text(text, encoding="utf-8")
        return DebuggerVariableDump(expression, text)

    def address_of(self, symbol: str) -> int:
        return self.symbols[symbol]

    def read_memory_array(self, address: int, length: int) -> bytes:
        return bytes(self.memory.get(address + i, 0) for i in range(length))

    def read_symbol_array(self, symbol: str, length: int) -> bytes:
        return self.read_memory_array(self.address_of(symbol), length)

    def snapshot(
        self,
        *,
        variables: tuple[str, ...] = (),
        registers: tuple[str, ...] = (),
        symbols: Mapping[str, int] | None = None,
    ) -> DebuggerSnapshot:
        return DebuggerSnapshot(
            state=self._state,
            variables={name: self.variables.get(name) for name in variables},
            registers={name: self.registers.get(name, 0) for name in registers},
            symbols={n: self.read_symbol_array(n, ln) for n, ln in (symbols or {}).items()},
        )

    def set_breakpoint(self, location: str, *args: Any, **kwargs: Any) -> FakeBreakpoint:
        del args, kwargs
        bp = self.breakpoints[location] = FakeBreakpoint(location, self)
        return bp

    def remove_breakpoint(self, location: str) -> None:
        self.breakpoints.pop(location, None)

    def clear_breakpoints(self) -> None:
        self.breakpoints.clear()

    def read_register(self, name: str) -> object:
        return self.registers.get(name, 0)

    def write_register(self, name: str, value: int) -> None:
        self.registers[name] = value

    def load_symbols(self, file: str | Path, *, no_code: bool = True) -> None:
        del file, no_code

    def run_script(
        self, file: str | Path, *arguments: object, timeout_s: float | None = None
    ) -> None:
        del file, arguments, timeout_s


class FakeDebuggerFactory:
    @contextmanager
    def open(
        self, context: Any, dependencies: Mapping[str, object], config: Mapping[str, Any]
    ) -> Iterator[FakeDebugger]:
        del context, dependencies
        yield FakeDebugger(config.get("variables"))
