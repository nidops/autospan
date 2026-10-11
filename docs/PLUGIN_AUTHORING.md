# Project Plugin Authoring

AutoSpan project plugins extend one host/runtime. They do not create a parallel runner and they do not act as a generic package mechanism for protocol drivers.

## Minimum project plugin

For a runnable starting point, generate and install the complete scaffold:

```bash
autospan new sample
python -m pip install -e ./autospan-sample
autospan test --config autospan-sample/autospan.yaml -- -q
```

This registration skeleton assumes a plan compiler and component factory defined
in the project package, as described below. It is a registration fragment; the
generated scaffold supplies those definitions and an executable example.

```python
from dataclasses import dataclass
from autospan.api import AutoSpanPlugin, PluginDescriptor, PluginRegistrar

@dataclass(frozen=True)
class MyPlugin(AutoSpanPlugin):
    descriptor: PluginDescriptor = PluginDescriptor(
        name="my-project",
        version="0.1.0",
        api_version=1,
    )

    def register(self, registrar: PluginRegistrar) -> None:
        registrar.plan_compiler(compile_plan)
        registrar.component_type("my_project.rig", MyFactory())

PLUGIN = MyPlugin()
```

`api_version` is a **literal compatibility target**, not the host's current
`AUTOSPAN_PLUGIN_API_VERSION` imported dynamically. When AutoSpan bumps the plugin API,
review the breaking changes and then update the literal deliberately. This makes an old
project fail fast instead of silently claiming compatibility with a newer host.

Expose it through:

```toml
[project.entry-points."autospan.plugins"]
my-project = "my_project.plugin:PLUGIN"
```

There is intentionally no `PluginDescriptor.requires`. Python/package dependencies install libraries; the project plugin imports the drivers it needs.

## Registration rules

`register()` is declaration-only. It MUST NOT:

- open hardware;
- start threads;
- import optional vendor DLLs merely because the project is installed;
- mutate global process state.

Hardware starts only when the compiled runtime is acquired.

## Plan compiler

A compiler translates project-owned config into generic `ComponentSpec` declarations. Planning must be side-effect free.

The remaining API examples are fragments. They assume project definitions such
as `MyRig`, `open_serial`, `send_heartbeat` and `run_doctor`, and imports from
`autospan.api` and `contextlib`. Use the generated project for complete code.

```python
def compile_plan(config, plan) -> None:
    plan.add_component(
        ComponentSpec(
            name="my-project.rig",
            type_id="my_project.rig",
            config={"port": config["port"]},
        )
    )
```

The project owns strict validation of its section. AutoSpan normalizes basic `KeyError`, `TypeError` and `ValueError` compiler failures into a project-scoped configuration error.

## Component factory and drivers

Factories receive resolved dependencies and immutable config, and return a context manager. Open plain driver libraries inside an `ExitStack`:

```python
class MyFactory:
    @contextmanager
    def open(self, context, dependencies, config):
        with ExitStack() as stack:
            serial = stack.enter_context(open_serial(config["port"]))
            device = MyDevice(serial)
            yield MyRig(device)
```

Drivers are ordinary Python libraries. They do not register with AutoSpan merely because they can be swapped internally.

## Resource claims

Declare physical/logical ownership in the plan so collisions fail before hardware opens.
Use the same canonical key for aliases of the same resource. Exclusive claims also
exclude other live runtimes in the same Python process; shared claims may coexist.
These reservations do not coordinate separate processes. Close other runtimes
sharing a resource before handing it to an external tool.

## Gates

```python
registrar.gate(
    GateContribution("flashing", "allow target reflashing", implies=("destructive",))
)
```

Scenarios opt in with markers; components use `ComponentSpec(..., gates=(...))`. A component touching real hardware should be gated so forgotten scenario markers cannot touch a bench accidentally.

## Runtime-owned periodic work

Do not create an untracked project scheduler for recurring host-side work. Factories and
project facades can use the scheduler owned by the active AutoSpan runtime:

```python
task = context.periodic.create(
    "my-project.heartbeat",
    interval_s=0.100,
    callback=send_heartbeat,
)

with task.lease(startup_timeout_s=1.0):
    ...
```

The first lease starts the worker and the final lease stops it. Nested users therefore
share one worker. `startup_timeout_s` is optional and waits for one successful first
callback when the project needs a readiness guarantee. A callback failure marks the task
failed and is reported by the lease/runtime unless project code releases all leases and
explicitly resets the failure.

AutoSpan owns only periodic execution. The callback, payload, target, retry policy and
protocol semantics remain project/driver code.

## Scenarios

```python
registrar.scenarios(
    ScenarioContribution(
        package="my_project",
        relative_path="scenarios",
        pytest_ini={"log_cli": True},
    )
)
```

Hosted runs read no ambient project `pytest.ini`. Unit tests for project internals remain in the project's normal `tests/` directory and are not AutoSpan scenarios.

Scenarios receive a function-scoped `runtime` fixture by default. Use the explicit
session-scoped `session_runtime` fixture when a suite shares one allocated bench
and firmware preparation. Both use the same plan, factories, gates, and evidence
store; use one allocation scope per bench in a run. The shared runtime closes
after all fixture reports and receives the aggregate verdict, including setup
and teardown failures. Recorders and artifact writers still use per-test evidence context.
A reset or reflash requested by a scenario stays
an explicit project operation.

## Commands and frontends

Commands and frontends are presentation/orchestration adapters over the same runtime. They must not create a second hardware lifecycle.

Console commands use `handler(ctx: CommandContext, args)` and return an integer
status or `None` for success. Use `ctx.runtime` for live components, `ctx.write()`
for output, and cooperative cancellation checks between safe driver operations.
The host serializes invocations and records return-code/exception verdicts.
Built-in command roots are reserved. See the working example and operator modes
in [Console](CONSOLE.md).

The initial public plugin contract is API 1. Commands take `CommandContext`,
not a bare runtime: use `ctx.runtime` and `ctx.write()`. Set the descriptor to
the literal `api_version=1` after checking this contract. Command completers receive
`(runtime, completed_args)` and must be fast, side-effect-free functions. Scenario
fixtures and provider contracts are independent of console output.

```python
def doctor(ctx: FrontendContext, args: tuple[str, ...]) -> int:
    with ctx.open_runtime() as runtime:
        return run_doctor(runtime.get("my-project.rig"), args)

registrar.frontend(FrontendContribution("my-project.doctor", doctor, "bench checks"))
```

## Evidence

Use structured events for low-rate lifecycle/control information. High-rate capture belongs to the driver/project recorder and is attached as a native artifact.

Only bind test/step context to workers whose work belongs to that scope. Long-lived
receivers should record unsolicited traffic with session context; see
[evidence](EVIDENCE.md).

## Testing a project plugin

```python
from autospan.testing import collect_contributions, compile_plugin

registry = collect_contributions(PLUGIN)
builder = compile_plugin(PLUGIN, {"port": "sim"}, config_dir=tmp_path)
assert [spec.name for spec in builder.components] == ["my-project.rig"]
```

`FactoryHarness` opens factories with a real evidence store for lifecycle tests.

## Contracts and providers

Providers are a **separate extension point**, not project plugins.

A provider package exposes one `ProviderContribution`:

```python
PROVIDER = ProviderContribution(FLASHER, "my-flasher", MyFlasherFactory())
```

and publishes it through:

```toml
[project.entry-points."autospan.providers"]
my-flasher = "my_flasher.autospan:PROVIDER"
```

The project selects it by name while compiling its plan:

```python
plan.add_component(
    provider_component(
        "target.flasher",
        FLASHER,
        config["flasher"],
        options=config.get("flash_options", {}),
        lazy=True,
        gates=("destructive",),
    )
)
```

Rules:

- merely installing a provider does not import or activate it;
- the host loads only provider names referenced by the compiled plan;
- missing/ambiguous/wrong-contract/wrong-version providers fail before hardware opens;
- `lazy=True` opens the provider only on first `.resolve()` and still closes it at runtime teardown;
- provider packages should run the matching conformance helper from `autospan.testing.conformance`;
- a new contract belongs in the kernel only when framework-level substitution is genuinely needed and the contract can stay vendor/project neutral.

## Compatibility

Project plugins target `AUTOSPAN_PLUGIN_API_VERSION`. AutoSpan refuses incompatible project-plugin API versions before planning/runtime startup.
