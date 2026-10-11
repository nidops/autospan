# AutoSpan architecture

## Ownership

> AutoSpan owns execution. A project plugin owns project meaning. Drivers are plain libraries. Swappable external tools are providers of narrow kernel contracts.

## Runtime model

There is one runtime owner. `RuntimeSession` is the internal lifecycle owner;
`Runtime` is the public live view over that same session. A project "workspace" is
only a directory containing project files/configuration, never a second runtime layer.
There is no separate `autospan-core` or `autospan-workspace` runtime product.

```text
config
  ↓
explicitly selected project plugin(s)
  ↓
declaration-only registration
  ↓
side-effect-free RuntimePlan
  ↓
provider resolution + DAG / claims validation
  ↓
dependency-first component acquisition
  ↓
one Runtime view
  ├── pytest
  ├── console
  └── Python/frontends
  │
  ├── runtime-owned periodic scheduler
  └── shared evidence sink
  ↓
stop periodic workers
  ↓
reverse-order component teardown
```

Merely installing a project plugin or provider never activates it.

Console modes share one dispatcher and the same session:

```text
plain / batch / Textual -> CommandShell -> CommandContext.runtime -> live components
                               |                |
                        verdict-aware step   explicit output / cancellation
                               |
                     finish/join command -> stop periodic workers -> teardown
```

Textual runs one command worker and renders a bounded output queue on its UI
thread. Busy submissions are rejected. A stop request never substitutes for
joining that worker before the existing runtime closes.

## Package boundary

The `autospan` wheel contains orchestration, generic contracts and frontends.
Protocol/device drivers are separate libraries imported by project packages.

The kernel may define a narrow contract for a swappable project-level tool such as a flasher or debugger. Implementations are separate provider packages advertised through `autospan.providers`.

## Project composition

A project plugin normally plans one project/bench component. Its factory opens the driver libraries it needs inside an `ExitStack` and returns a project facade/rig:

```text
project plugin -> compile plan -> project component -> open drivers -> project rig
```

Do not explode every driver/client into a kernel component merely because `RuntimePlan` exists. Use separate components when independent lifecycle/dependency/resource ownership actually requires it.

## Extension categories

AutoSpan has two framework-level extension categories:

1. **Project plugin** (`autospan.plugins`) — configuration compilation, project component factories, scenarios, commands, gates, frontends and simulation policy.
2. **Provider** (`autospan.providers`) — one implementation of a narrow kernel contract such as `flasher` or `debugger`.

Drivers use normal Python imports. They have no AutoSpan entry point, project config section or plugin dependency declaration.

## Periodic execution

Generic recurring execution belongs to the runtime; protocol meaning does not.
Projects create named periodic tasks from `context.periodic` or `runtime.periodic`.
Tasks use monotonic scheduling, nested leases, deterministic shutdown, failure
propagation and basic timing statistics. A lease can optionally wait for one successful
first callback (`startup_timeout_s`) when a project needs a generic readiness boundary.
AutoSpan never knows whether the callback is network management, a diagnostic keep-alive,
serial polling or something else.

Periodic workers are stopped before component teardown so a worker cannot continue
using a driver after its resource has closed. This is host-side orchestration, not a
hard-real-time scheduler.
Callbacks must return for shutdown to finish. A slow-stop deadline reports failure;
the owner still waits for the worker instead of closing its resources underneath it.

## Evidence

Control-plane events are small and structured. High-rate data stays in the owning
driver/project recorder and is committed as an opaque artifact. Artifact formats are
open-ended; the evidence store records metadata, correlation, completeness, size and
hash without parsing the traffic format.

## Lifecycle

Every component is acquired through a context manager. AutoSpan uses dependency order for startup and reverse order for cleanup. Partial startup failures roll back already-opened components.

## Pytest

AutoSpan invokes pytest with explicitly contributed scenario paths and one bridge plugin. Third-party pytest-plugin autoloading is disabled for hosted runs.

The function-scoped `runtime` fixture acquires per test for hardware isolation.
The explicit `session_runtime` fixture acquires once for a shared bench. Use one
allocation scope per bench; both fixtures use the same owner model.

## Gates

The host always knows `hardware` and `destructive` (`destructive` implies `hardware`). Project plugins may contribute more. Gates are enforced both on scenarios and on component acquisition, before a gated factory can touch hardware.

## Frontends

`autospan run NAME` executes a project-contributed frontend using the same plan/runtime owner. A frontend must not create a parallel hardware lifecycle.

## Outcome

Function-scoped teardown sees the setup/call verdict known at that point; its own
teardown report arrives later. The session runtime closes after all fixture reports
and receives the aggregate outcome, including setup and teardown failures. The
run manifest records the final pytest status. Factories can retain failure-only
artifacts using `ComponentContext.outcome`.

## Non-goals

- no runtime Git/package fetching;
- no plugin dependency graph for drivers;
- no distributed executor;
- no hard-real-time guarantee (including periodic workers);
- no universal transport or device hierarchy;
- no protocol semantics in the kernel.
