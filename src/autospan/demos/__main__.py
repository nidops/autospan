"""Launch the hardware-free AutoSpan Lab through the existing shell and TUI."""

from __future__ import annotations

import argparse
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator

from autospan.api import CommandShell

from .lab import make_shell


@contextmanager
def _output_directory(path: Path | None) -> Iterator[Path]:
    if path is not None:
        path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            raise OSError(f"Not a directory: {path}")
        yield path
    else:
        with TemporaryDirectory(prefix="autospan-lab-") as directory:
            yield Path(directory)


def _execute(shell: CommandShell, line: str) -> int:
    outcome = shell.execute(line, output=lambda text: print(text, end="", flush=True))
    return int(outcome.status)


def _plain_repl(shell: CommandShell) -> int:
    print("AutoSpan Lab | SIMULATION ONLY | type 'lab tour' or 'exit'")
    status = 0
    while True:
        try:
            line = input("lab> ").strip()
        except EOFError:
            print()
            break
        except KeyboardInterrupt:
            print("\n^C (input cleared)")
            continue
        if not line:
            continue
        outcome = shell.execute(line, output=lambda text: print(text, end="", flush=True))
        status = shell.status
        if outcome.exit:
            break
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Try AutoSpan without hardware, config or plugins")
    parser.add_argument(
        "--check", action="store_true", help="Check that the bundled Lab is importable"
    )
    parser.add_argument("--exec", metavar="COMMAND", help="Execute one Lab command and exit")
    parser.add_argument("--plain", action="store_true", help="Use a text REPL without Textual")
    parser.add_argument("--seed", type=int, default=7, help="Reproducible virtual telemetry seed")
    parser.add_argument(
        "--keep-output", metavar="DIR", type=Path, help="Retain console log exports here"
    )
    parser.add_argument(
        "--developer", action="store_true", help="Show bounded non-cooperative driver test"
    )
    args = parser.parse_args(argv)
    if args.check:
        with TemporaryDirectory(prefix="autospan-lab-check-") as directory:
            shell = make_shell(Path(directory), seed=args.seed, developer=args.developer)
            if len(shell.commands) not in (8, 9):
                parser.error("Demo command registration is incomplete")
        print("OK: AutoSpan Lab | 3 simulated devices | 4 scenarios | no hardware")
        return 0
    try:
        with _output_directory(args.keep_output) as root:
            shell = make_shell(root, seed=args.seed, developer=args.developer)
            if args.exec is not None:
                return _execute(shell, args.exec)
            if args.plain:
                return _plain_repl(shell)
            try:
                from autospan.frontends.tui.app import ConsoleApp
            except ImportError as exc:
                if exc.name and exc.name.split(".")[0] == "textual":
                    parser.error(
                        'Interactive Lab needs Textual. Install with: pip install "autospan[tui]"'
                    )
                raise

            class LabApp(ConsoleApp):
                TITLE = "AutoSpan | Lab (simulated)"

                def on_mount(self) -> None:
                    super().on_mount()
                    self.record("AutoSpan Lab | SIMULATION ONLY | no hardware or credentials")
                    self.record("Try: lab tour | lab run smoke | lab run endurance (Ctrl+C)")
                    self.record("Tip: type 'lab ' and press Ctrl+Space for contextual suggestions")
                    self.record("Exports: Ctrl+S writes console.log in the demo output directory")

            app = LabApp(shell)
            try:
                app.run()
            finally:
                app.wait_for_command()
            return app.status
    except OSError as exc:
        print(f"AutoSpan Lab: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
