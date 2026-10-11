# Design principles

AutoSpan is a small, protocol-neutral execution kernel. Projects own their bench
configuration, drivers, scenarios, simulation policy and operator commands.

## Composition and extension points

Project plugins register declarative contributions through `autospan.plugins`.
Only projects named in the configuration are activated. Registration and planning
are hardware-free; discovery lists installed entry points without importing them.

Drivers are ordinary Python libraries with their own APIs, context managers and
tests. Project component factories compose them and return project facades.
Dependencies are passed at the composition root; component internals do not use
the runtime registry as a service locator.

Swappable external tools use `autospan.providers`. A provider contract belongs in
the kernel only when real substitution needs justify shared lifecycle semantics
that remain independent of vendors and projects. Providers are selected by the
compiled plan and contribute tools rather than project scenarios or frontends.

## One runtime owner

`RuntimePlan` is an immutable description of components, dependencies, claims and
gates. Acquisition follows the dependency graph. Partial startup rolls back, and
teardown attempts every owner in reverse order.

The internal `RuntimeSession` owns the live components and periodic work.
`Runtime` is the public view passed to scenarios and frontends. A workspace is a
project directory, not another runtime layer. Hosted pytest, the console, Python
and contributed frontends all use the same ownership model.

Gates are explicit data and are checked before component factories run. Hardware
and destructive operations require their gates; projects may add more. Hosted
pytest uses contributed scenarios and explicit operator configuration. Overrides
use `--set dotted.key=value`. The test verdict reaches components before teardown
so evidence retention can depend on the outcome.

## Background work and evidence

The public lifecycle boundary is synchronous. Drivers may own internal workers;
reusable periodic execution belongs to the runtime. Projects supply the callback,
while AutoSpan owns scheduling, leases, bounded startup, timing and failure
reporting. Periodic workers stop before components close.

Evidence retains low-rate control events and opaque artifacts with context, size
and SHA-256 metadata. The evidence run can span multiple test-scoped runtime
sessions. Producers retain ownership of their native traffic formats and decoding;
high-rate data does not pass through a kernel packet model.

See [architecture](ARCHITECTURE.md), [plugin authoring](PLUGIN_AUTHORING.md) and
[evidence](EVIDENCE.md) for the public contracts and examples.
