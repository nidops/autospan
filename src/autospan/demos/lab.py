"""Protocol-neutral, no-I/O console playground for the AutoSpan shell.

This is a *simulation* and a shell/TUI example, not a hardware project plugin.
Do not represent its operations as qualification or actual bench evidence.
"""

from __future__ import annotations

import random
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from autospan.api import CommandContext, CommandContribution, CommandShell, Runtime
from autospan.api.commands import ArgumentCompleter

from .evidence import SimulationEvidence


@dataclass
class LabState:
    selected: str = "sensor-a"
    seed: int = 7
    values: dict[str, dict[str, str]] = field(
        default_factory=lambda: {
            "controller": {"firmware": "1.4.2", "mode": "standby", "uptime": "00:42:17"},
            "sensor-a": {"temperature": "23.8 C", "humidity": "41 %", "sample_rate": "10 Hz"},
            "power": {"voltage": "12.4 V", "current": "0.18 A", "relay": "off"},
        }
    )


WRITABLE: dict[str, dict[str, tuple[str, ...]]] = {
    "controller": {"mode": ("standby", "active")},
    "sensor-a": {"sample_rate": ("1 Hz", "5 Hz", "10 Hz", "20 Hz", "50 Hz")},
    "power": {"relay": ("off", "on")},
}

PROFILES = {
    "smoke": (
        "Initialize controller",
        "Read sensor-a",
        "Exercise virtual relay",
        "Verify restored state",
    ),
    "diagnostic": (
        "Discover virtual devices",
        "Identify controller",
        "Read current state",
        "Check response",
        "Verify restored state",
    ),
    "endurance": (
        "Start monitor",
        "Heartbeat",
        "Sample telemetry",
        "Heartbeat",
        "Check consistency",
        "Verify restored state",
    ),
    "failure": (
        "Initialize controller",
        "Inject expected synthetic fault",
        "Verify restored state",
    ),
}


def make_shell(root: Path, *, seed: int = 7, developer: bool = False) -> CommandShell:
    """Create an isolated shell; never discover or acquire project plugins."""
    state = LabState(seed=seed)

    def require_no_args(ctx: CommandContext, args: tuple[str, ...]) -> bool:
        if args:
            ctx.write("This command takes no arguments. Try: lab tour")
            return False
        return True

    def tour(ctx: CommandContext, args: tuple[str, ...]) -> int:
        if not require_no_args(ctx, args):
            return 2
        ctx.write("\nAutoSpan Lab  |  SIMULATED: no hardware, drivers or credentials")
        ctx.write("  lab devices              Explore three virtual components")
        ctx.write("  lab select sensor-a       Select a component")
        ctx.write("  lab read temperature      Read a virtual parameter")
        ctx.write("  lab set sample_rate 20    Change a simulated setting (Hz)")
        ctx.write("  lab run smoke             Run a short cooperative scenario")
        ctx.write("  lab run endurance         Press Ctrl+C to request safe stopping")
        ctx.write("  lab stream                Exercise streaming and log navigation")
        ctx.write("  Ctrl+Space: suggestions here  |  Ctrl+P: global command search")
        return 0

    def devices(ctx: CommandContext, args: tuple[str, ...]) -> int:
        if not require_no_args(ctx, args):
            return 2
        ctx.write("\nDEVICE        KIND          STATE")
        for name, kind in (
            ("controller", "controller"),
            ("sensor-a", "sensor"),
            ("power", "power"),
        ):
            marker = " *" if name == state.selected else ""
            ctx.write(f"{name:<14}{kind:<14}ready{marker}")
        return 0

    def status(ctx: CommandContext, args: tuple[str, ...]) -> int:
        if not require_no_args(ctx, args):
            return 2
        ctx.write(f"Lab ready | selected={state.selected} | simulation=yes | seed={state.seed}")
        ctx.write("No real components were acquired.")
        return 0

    def select(ctx: CommandContext, args: tuple[str, ...]) -> int:
        if len(args) != 1 or args[0] not in state.values:
            ctx.write("Select one device: " + ", ".join(state.values))
            return 2
        state.selected = args[0]
        ctx.write(f"Selected {state.selected}")
        return 0

    def read(ctx: CommandContext, args: tuple[str, ...]) -> int:
        values = state.values[state.selected]
        if len(args) != 1 or args[0] not in values:
            ctx.write(f"Read one {state.selected} parameter: " + ", ".join(values))
            return 2
        ctx.write(f"{state.selected}.{args[0]} = {values[args[0]]}")
        return 0

    def set_value(ctx: CommandContext, args: tuple[str, ...]) -> int:
        writable = WRITABLE[state.selected]
        if len(args) != 2 or args[0] not in writable:
            ctx.write(
                f"Writable parameters on {state.selected}: " + (", ".join(writable) or "none")
            )
            return 2
        key, value = args
        allowed = writable[key]
        if key == "sample_rate" and value.isdecimal():
            value += " Hz"
        if value not in allowed:
            ctx.write(f"Invalid {key!r} value. Choose: " + ", ".join(allowed))
            return 2
        state.values[state.selected][key] = value
        ctx.write(f"SET {state.selected}.{key} = {value} (simulation only)")
        return 0

    def run(ctx: CommandContext, args: tuple[str, ...]) -> int:
        if len(args) != 1 or args[0] not in PROFILES:
            ctx.write("Choose a scenario: " + ", ".join(PROFILES))
            return 2
        name = args[0]
        steps = PROFILES[name]
        # Restore the locally simulated relay even on failure or cancellation.
        relay_before = state.values["power"]["relay"]
        ctx.write(f"\nRUN {name} | SIMULATED | Ctrl+C requests cancellation")
        try:
            for index, step in enumerate(steps, 1):
                ctx.check_cancelled()
                ctx.write(f"  [{index}/{len(steps)}] {step} ... ")
                ctx.wait(0.20 if name != "endurance" else 1.0)
                ctx.check_cancelled()
                if name == "failure" and index == 2:
                    ctx.write("FAIL (intentional simulated fault)")
                    return 1
                if name == "smoke" and index == 3:
                    state.values["power"]["relay"] = "on"
                if index == len(steps):
                    state.values["power"]["relay"] = relay_before
                ctx.write("  OK")
            ctx.write(f"PASS {name} (simulated)")
            return 0
        finally:
            state.values["power"]["relay"] = relay_before
            ctx.write("CLEANUP OK: simulated relay restored; no hardware owned")

    def stream(ctx: CommandContext, args: tuple[str, ...]) -> int:
        if args:
            ctx.write("Usage: lab stream")
            return 2
        rng = random.Random(state.seed)
        ctx.write("Virtual telemetry stream; Ctrl+C requests stop")
        for index in range(60):
            ctx.check_cancelled()
            ctx.wait(0.10)
            ctx.check_cancelled()
            ctx.write(
                f"sample {index:03d} | sensor-a | temperature={23.0 + rng.randrange(20) / 10:.1f} C"
            )
        ctx.write("END of virtual stream")
        return 0

    def stubborn(ctx: CommandContext, args: tuple[str, ...]) -> int:
        if args:
            return 2
        ctx.write("Simulated uninterruptible driver for 2 seconds. Ctrl+C cannot kill the worker.")
        time.sleep(2.0)  # Intentionally bounded; available only in --developer mode.
        ctx.write("Driver returned. Runtime owner may now finish.")
        return 0

    def first_arg(choices: Iterable[str]) -> ArgumentCompleter:
        return lambda _runtime, completed: tuple(choices) if not completed else ()

    def complete_read(_runtime: Runtime, completed: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(state.values[state.selected]) if not completed else ()

    def complete_set(_runtime: Runtime, completed: tuple[str, ...]) -> tuple[str, ...]:
        allowed = WRITABLE[state.selected]
        if not completed:
            return tuple(allowed)
        if len(completed) == 1:
            return allowed.get(completed[0], ())
        return ()

    commands = [
        CommandContribution(("lab", "tour"), tour, help="Guided first steps"),
        CommandContribution(("lab", "devices"), devices, help="Discover virtual components"),
        CommandContribution(("lab", "status"), status, help="Inspect lab state"),
        CommandContribution(
            ("lab", "select"), select, help="Choose a component", complete=first_arg(state.values)
        ),
        CommandContribution(
            ("lab", "read"), read, help="Read a virtual parameter", complete=complete_read
        ),
        CommandContribution(
            ("lab", "set"),
            set_value,
            help="Change an allowed virtual setting",
            complete=complete_set,
        ),
        CommandContribution(
            ("lab", "run"), run, help="Run a simulated scenario", complete=first_arg(PROFILES)
        ),
        CommandContribution(("lab", "stream"), stream, help="Stream virtual telemetry"),
    ]
    if developer:
        commands.append(
            CommandContribution(
                ("lab", "stubborn"), stubborn, help="Test a bounded non-cooperative driver"
            )
        )
    return CommandShell(Runtime({}, SimulationEvidence(root)), tuple(commands))
