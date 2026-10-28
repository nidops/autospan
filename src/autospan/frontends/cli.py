"""AutoSpan command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from autospan.api.errors import AutoSpanError
from autospan._core.config import apply_overrides, load_config, selected_plugins
from autospan._core.discovery import discover_plugins
from autospan._core.provider_discovery import discover_providers
from autospan._core.host import HostBuild, build_host


def _add_config(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, required=True, help="AutoSpan YAML configuration")
    parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="override a dotted config key (YAML value), e.g. --set my_project.bench=b.yaml",
    )


def _add_runtime_gates(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--hardware",
        action="store_true",
        help="enable the built-in hardware gate for this frontend runtime",
    )
    parser.add_argument(
        "--destructive",
        action="store_true",
        help="enable the built-in destructive gate (also enables hardware)",
    )
    parser.add_argument(
        "--gate",
        dest="gates",
        action="append",
        default=[],
        metavar="NAME",
        help="enable a project-contributed gate for this frontend runtime (repeatable)",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autospan")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "plugins", help="list installed AutoSpan project plugins (without importing them)"
    )
    sub.add_parser("providers", help="list installed AutoSpan providers (without importing them)")

    new = sub.add_parser("new", help="scaffold a project plugin (plugin, config, smoke scenario)")
    new.add_argument("name", help="project name, lowercase words joined by '-', e.g. acme-bench")
    new.add_argument("--dir", type=Path, default=Path("."), help="parent directory (default: .)")

    plan = sub.add_parser("plan", help="compile and validate a runtime plan without hardware")
    _add_config(plan)

    test = sub.add_parser("test", help="run selected plugin scenarios through pytest")
    _add_config(test)
    test.add_argument("--evidence-dir", type=Path, default=Path("artifacts/evidence"))
    test.add_argument("pytest_args", nargs=argparse.REMAINDER)

    console = sub.add_parser("console", help="open the generic console on the same runtime")
    _add_config(console)
    console.add_argument("--evidence-dir", type=Path, default=Path("artifacts/evidence"))
    _add_runtime_gates(console)
    console.add_argument(
        "--exec",
        dest="commands",
        action="append",
        default=[],
        metavar="LINE",
        help="run this command line non-interactively (repeatable)",
    )
    console.add_argument(
        "--script", type=Path, help="run command lines from a file (# comments allowed)"
    )
    console.add_argument(
        "--plain",
        action="store_true",
        help="use the line console even when the terminal UI is available",
    )
    console.add_argument(
        "--keep-going", action="store_true", help="do not stop a batch at the first failure"
    )

    run = sub.add_parser("run", help="run a plugin-contributed frontend (see `autospan frontends`)")
    run.add_argument("frontend", help="frontend name, e.g. my_project.console")
    _add_config(run)
    run.add_argument("--evidence-dir", type=Path, default=Path("artifacts/evidence"))
    _add_runtime_gates(run)
    # Frontend arguments follow a literal ``--`` (split before argparse runs).

    frontends = sub.add_parser(
        "frontends", help="list frontends/gates contributed by selected plugins"
    )
    _add_config(frontends)
    return parser


def _host_from_config(path: Path, overrides: Sequence[str] = ()) -> HostBuild:
    config = apply_overrides(load_config(path), overrides)
    names = selected_plugins(config)
    resolved = path.resolve()
    return build_host(
        plugin_names=names,
        config=config,
        config_dir=resolved.parent,
        config_path=resolved,
    )


def _strip_separator(args: Sequence[str]) -> tuple[str, ...]:
    items = list(args)
    if items and items[0] == "--":
        items = items[1:]
    return tuple(items)


def _plain(value: object) -> object:
    """Thaw the plan's frozen containers so ``autospan plan`` prints real JSON."""
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _plan_payload(host: HostBuild) -> dict[str, object]:
    return {
        "plugins": [
            {"name": loaded.name, "distribution": loaded.distribution} for loaded in host.plugins
        ],
        "components": [
            {
                "name": spec.name,
                "type": spec.type_id,
                "dependencies": list(spec.dependencies),
                "claims": [asdict(claim) for claim in spec.claims],
                "gates": list(spec.gates),
                "config": _plain(spec.config),
            }
            for spec in host.plan.components
        ],
        "scenarios": [
            {
                "package": item.package,
                "relative_path": item.relative_path,
                "pytest_ini": dict(item.pytest_ini),
            }
            for item in host.plan.scenarios
        ],
        "gates": [
            {"name": gate.name, "implies": list(gate.implies), "help": gate.help}
            for gate in host.gates
        ],
        "frontends": [frontend.name for frontend in host.frontends],
        "metadata": _plain(host.plan.metadata),
    }


def _enabled_frontend_gates(host: HostBuild, args: argparse.Namespace) -> frozenset[str]:
    requested = list(getattr(args, "gates", ()) or ())
    if getattr(args, "hardware", False):
        requested.append("hardware")
    if getattr(args, "destructive", False):
        requested.append("destructive")

    enabled: set[str] = set()
    for name in requested:
        try:
            enabled.update(host.gate_closure(name))
        except KeyError as exc:
            available = ", ".join(gate.name for gate in host.gates) or "<none>"
            raise AutoSpanError(f"Unknown gate {name!r}; available: {available}") from exc
    return frozenset(enabled)


def _split_passthrough(argv: Sequence[str]) -> tuple[list[str], tuple[str, ...]]:
    items = list(argv)
    if "--" in items:
        index = items.index("--")
        return items[:index], tuple(items[index + 1 :])
    return items, ()


def main(argv: Sequence[str] | None = None) -> int:
    head, passthrough = _split_passthrough(sys.argv[1:] if argv is None else argv)
    args = _parser().parse_args(head)
    try:
        if args.command == "plugins":
            for plugin in discover_plugins():
                dist = f" ({plugin.distribution})" if plugin.distribution else ""
                print(f"{plugin.name}: {plugin.value}{dist}")
            return 0

        if args.command == "providers":
            for provider in discover_providers():
                dist = f" ({provider.distribution})" if provider.distribution else ""
                print(f"{provider.name}: {provider.value}{dist}")
            return 0

        if args.command == "new":
            from autospan.frontends.scaffold import new_project

            root = new_project(args.name, args.dir)
            print(f"created {root}")
            print(
                f"next: pip install -e {root}[dev] && autospan test --config {root / 'autospan.yaml'}"
            )
            return 0

        host = _host_from_config(args.config, args.overrides)
        if args.command == "plan":
            print(json.dumps(_plan_payload(host), indent=2, default=str))
            return 0
        if args.command == "frontends":
            for frontend in host.frontends:
                print(f"{frontend.name:28} {frontend.help}")
            for gate in host.gates:
                implied = f" (implies {', '.join(gate.implies)})" if gate.implies else ""
                print(f"{gate.option:28} {gate.help}{implied}")
            return 0
        if args.command == "test":
            from autospan.frontends.test_command import run_tests

            return run_tests(
                host=host,
                evidence_root=args.evidence_dir,
                pytest_args=_strip_separator(args.pytest_args) + passthrough,
            )
        if args.command == "console":
            from autospan.frontends.console import run_console

            return run_console(
                host=host,
                evidence_root=args.evidence_dir,
                commands=args.commands,
                script=args.script,
                keep_going=args.keep_going,
                plain=args.plain,
                enabled_gates=_enabled_frontend_gates(host, args),
            )
        if args.command == "run":
            from autospan.frontends.frontend_host import HostFrontendContext

            frontend = host.frontend(args.frontend)
            context = HostFrontendContext(
                host,
                evidence_root=args.evidence_dir,
                enabled_gates=_enabled_frontend_gates(host, args),
                frontend_name=frontend.name,
            )
            return int(frontend.handler(context, passthrough) or 0)
    except AutoSpanError as exc:
        print(f"autospan: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
