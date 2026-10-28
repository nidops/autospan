# AutoSpan

Python runtime for reproducible hardware testing, resource ownership and evidence.

AutoSpan plans and runs host-side tests with explicit resource ownership, predictable
cleanup and evidence. Project plugins supply the configuration, drivers, scenarios
and commands for a bench. The core stays independent of protocols, vendors and projects.

```text
configuration -> project plugin -> runtime plan -> components
                                                   |
                                      scenarios / console / Python
                                                   |
                                            events and artifacts
```

## Quick start

Python 3.11 or newer is required. The package is `autospan`; use the `test` and
`tui` extras for pytest and Textual. Install the published package and create a small
project with an installed smoke scenario:

These commands use a POSIX shell. On Windows, create the environment with
`python -m venv .venv` and activate it with `.venv\Scripts\Activate.ps1` in PowerShell.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install 'autospan[test,tui]==0.2.0'
autospan new sample
python -m pip install -e './autospan-sample[dev]'
autospan plan --config autospan-sample/autospan.yaml
autospan test --config autospan-sample/autospan.yaml -- -q
autospan console --config autospan-sample/autospan.yaml --plain --exec "sample status"
```

Interactive Textual use requires the `tui` extra and a TTY; the quick start above
runs in batch mode.

Planning validates configuration, dependencies and claims without opening hardware.
The sample uses no hardware. For a hardware project, enable the gate explicitly:

```bash
autospan test --config bench.local.yaml -- --hardware -q
autospan console --config bench.local.yaml --hardware
```

For `test`, arguments after `--` go to pytest. Console and contributed frontends
accept their gate options before `--`. Installing a plugin does not activate it;
its name must appear in the configuration.

## What the runtime owns

- Immutable plans, dependency ordering and resource-claim validation.
- Component startup, rollback after partial failure and reverse-order teardown.
- One runtime model shared by hosted pytest, the console and Python frontends.
- Hardware and destructive gates, with optional project gates.
- Generic periodic tasks with leases, startup checks and failure reporting.
- Structured evidence and opaque files with correlation, size and SHA-256 metadata.

Plugins use the public `autospan.api` package. `autospan._core` is internal.
Extensions are trusted Python code; the host is not a sandbox or a real-time scheduler.

## Extension points

A project plugin owns bench wiring, scenarios, commands and simulation policy:

```toml
[project.entry-points."autospan.plugins"]
my-project = "my_project.plugin:PLUGIN"
```

Plugins declare API version `1`. Their component factories open ordinary driver
libraries and return project facades. Drivers do not need an AutoSpan entry point.

Swappable tools such as flashers and debuggers use a separate provider group:

```toml
[project.entry-points."autospan.providers"]
my-flasher = "my_flasher.autospan:PROVIDER"
```

A plan selects a provider by name through `provider_component(...)`. Discovery is
lazy: `autospan plugins` and `autospan providers` list installed extensions without
importing them.

Commands use `CommandContext` for the live runtime, explicit output and cooperative
cancellation. The optional `tui` extra supplies history, completion, a command
picker and log search/export; Quit waits for the active operation. See the
[console manual](https://github.com/nidops/autospan/blob/master/docs/CONSOLE.md).

## Evidence

Runs write `events.jsonl` and `manifest.json` under `artifacts/evidence/<run-id>/`.
Use evidence steps for meaningful actions. Keep high-rate traffic in its recorder
and attach the resulting file. Worker context must be bound explicitly and must
match the lifetime of the work being recorded.

## Documentation

- [Architecture](https://github.com/nidops/autospan/blob/master/docs/ARCHITECTURE.md) and [design principles](https://github.com/nidops/autospan/blob/master/docs/DESIGN.md).
- [Plugin authoring](https://github.com/nidops/autospan/blob/master/docs/PLUGIN_AUTHORING.md) and [scenario authoring](https://github.com/nidops/autospan/blob/master/docs/SCENARIO_MANUAL.md).
- [Configuration](https://github.com/nidops/autospan/blob/master/docs/CONFIGURATION.md), [console](https://github.com/nidops/autospan/blob/master/docs/CONSOLE.md) and [evidence](https://github.com/nidops/autospan/blob/master/docs/EVIDENCE.md).
- [Development](https://github.com/nidops/autospan/blob/master/docs/DEVELOPING.md) and [releasing](https://github.com/nidops/autospan/blob/master/RELEASING.md).
- [Extension trust](https://github.com/nidops/autospan/blob/master/docs/PLUGIN_TRUST.md) and [security](https://github.com/nidops/autospan/blob/master/SECURITY.md).

MIT. See [LICENSE](https://github.com/nidops/autospan/blob/master/LICENSE).
