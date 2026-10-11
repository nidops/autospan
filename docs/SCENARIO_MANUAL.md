# AutoSpan Scenario Authoring Manual

> **Goal:** scenarios read like requirements, reusable behavior exists once, and pytest, the console, and scripts operate on the same AutoSpan runtime/components.

Use this guide when writing scenarios for an AutoSpan project plugin. Keep the
requirement, operation and observed result visible to a reviewer.

## 1. Mental model

```text
Requirement / operator intent
          |
          v
pytest scenario / console / script
          |
          | runtime.require(...)
          v
project facade / operations       <- project meaning and driver composition
          |
          v
driver / provider -> hardware / simulator

AutoSpan owns the component lifecycle and shared evidence.
```

**One implementation, multiple frontends.** Pytest and the console may present an operation differently, but they must not reimplement it.

## 2. Ownership by layer

| Layer | Owns | Must not own |
|---|---|---|
| Scenario | requirement intent, scenario order, final verdict | transport mechanics, vendor APIs, security algorithms |
| Fixture | setup/teardown lifecycle, bound facades | the requirement verdict |
| Project facade/operation | reusable project workflow and project semantics | pytest lifecycle, duplicate protocol stacks |
| Pure assertion helper | comparisons only | hardware I/O or state changes |
| AutoSpan runtime | configured component graph, lifecycle, evidence access | customer topology/meaning |
| Driver library | protocol/resource mechanics | project policy or a second runtime |
| Config | topology, IDs, timings, implementation selection | executable business/test logic |
| Project plugin | configuration, driver composition and contributions | a second runtime, pytest engine or evidence store |
| Provider/vendor adapter | narrow tool contract or vendor translation | project orchestration |
| Console | presentation and command parsing | separate hardware ownership |

If two layers can reasonably claim the same behavior, stop and pick one owner.

## 3. Canonical scenario shape

Choose one allocation scope per bench. The function-scoped `runtime` fixture
acquires and closes components for each test. The explicit `session_runtime`
fixture acquires once and closes after all fixture teardown reports, with an
aggregate verdict. Both use the same runtime owner, gates and evidence store.
Per-test evidence correlation still applies to operations on a shared runtime.

The following fragment assumes project fixtures `ecu` and `expected`:

```python
def test_requirement(ecu, runtime) -> None:
    """The ECU shall preserve configuration after reset."""

    with runtime.evidence.step("Write configuration"):
        ecu.write_configuration(expected)

    with runtime.evidence.step("Reset target"):
        ecu.reset()

    with runtime.evidence.step("Read configuration"):
        actual = ecu.read_configuration()

    assert actual == expected
```

A reviewer should see **prepare -> stimulate -> observe -> verdict** without reading
transport internals.

### Scenarios MUST

1. Have a one-sentence requirement-style docstring.
2. Keep meaningful actions visible with `runtime.evidence.step(...)`.
3. Keep the important verdict visible in the test or in a **pure** assertion helper.
4. Use project facades/typed identifiers when available.
5. Restore destructive state through fixtures/context managers when practical.
6. Be runnable without importing source-tree-only modules.

### Scenarios SHOULD NOT

- contain seed/key/CMAC algorithms;
- know backend/provider internals;
- duplicate CAN-ID or addressing arithmetic already owned by config/project code;
- call private driver helpers;
- use vague helpers such as `check_everything()`;
- sleep blindly when a deterministic wait/query primitive exists.

## 4. Helper taxonomy

Every reusable helper should fit one category.

### Query

Reads/observes and returns data. It MUST NOT mutate the DUT as a hidden side effect.

```python
value = project.read_version()
frame = project.wait_for_status()
```

### Command

Changes state and reports protocol/operation success or failure.

```python
project.enter_extended_session()
project.write_configuration(value)
```

Names must be honest. `write_and_verify()` is invalid if it only writes and reads back without comparing.

### Wait

Observes until a condition or timeout. A helper whose name contains **all**, **must**, **require**, or **expect** must fail when that contract is not met.

### Pure assertion

Receives already-observed values and compares them. It performs no hardware I/O.

### Composite project operation

Combines reusable driver/library operations into one stable project workflow. It may own protocol details while keeping the scenario understandable.

## 5. Resource ownership

The outermost public project operation owns the lifecycle/lease needed for the operation it exposes. Do not make scenarios stack hidden leases by calling an operation inside another public operation that acquires the same resource again.

Internal `_..._open()` or equivalent primitives are acceptable when a composite operation already owns the resource, but they stay internal.

## 6. Reusable driver/library versus project policy

Reusable driver/library examples:

```text
CAN send/receive/fan-out
ISO-TP segmentation/reassembly
UDS request/response, NRC 0x78, DID/Routine/DTC mechanics
UART read/write
```

Project policy examples:

```text
which sessions exist
which DIDs/RIDs/DTCs matter
security/CMAC algorithm
wake/sleep workflow
which ECU/node should be addressed
external flash/debug tool project procedure
pass/fail expectations
```

Project code composes driver libraries and provider-backed tools. It does not subclass the kernel merely because a workflow is unusual.

## 7. Read, write, decode, compare

Keep the stages distinct:

```text
read raw -> decode -> compare
write raw -> read back -> decode -> compare
```

Do not hide all four behind a vague helper.

## 8. Repeated requirements use parametrization

If only data changes, parameterize the scenario. Keep separate scenarios when behavior, setup, or verdict genuinely differs.

## 9. Fixtures own lifecycle, not verdicts

Fixtures SHOULD own:

- construction of bound project facades;
- cleanup/restoration;
- external simulator setup;
- temporary resources genuinely needed by a test.

Fixtures SHOULD NOT perform the main requirement action before the test body begins.

## 10. Evidence steps

Use a step for actions a reviewer cares about:

```text
1. Enter extended session
2. Write configuration
3. Reset DUT
4. Verify persistence
```

Do not create a step for every Python line.

Artifacts generated inside a step inherit `run_id`, `test_id`, and `step_id` automatically on the owning thread.

### Worker threads

Python context variables do not automatically propagate to newly created threads.
Capture and bind context when a worker's work belongs to that test or step:

```python
ctx = runtime.evidence.current_context


def worker():
    with runtime.evidence.bind_context(ctx):
        runtime.evidence.event("capture_started", source="my-plugin")
```

High-rate traffic must still stay in the owning recorder and be attached as a file/artifact; do not emit one AutoSpan event per CAN/BLE/Ethernet packet.

A receiver that outlives the test or step should use session context for unsolicited
traffic. It must not keep attributing later records to the scope in which it started.

## 11. Configuration

Put data in configuration when it varies by project/bench/release:

- endpoint topology;
- CAN IDs / addresses;
- DID/RID/DTC definitions;
- timeouts that are policy;
- provider/backend selection;
- paths to firmware/scripts/databases;
- feature flags.

Do not put executable algorithms in YAML merely to call them configurable. Configuration selects implementations; implementations stay typed and tested.

Relative file paths should resolve from the AutoSpan configuration file directory, not the operator's current working directory.

## 12. Console standard

The console is another frontend to the same runtime.

A project plugin may contribute commands such as:

```text
project target wake
project target status
project target reset
```

Those commands use the same runtime components/project operations used by scenarios. They must not open a parallel hardware session.

## 13. Scenario package versus plugin unit tests

Keep them separate:

```text
my_plugin/
├── src/my_plugin/...
├── tests/                 # plugin development/unit/contract tests
└── src/my_plugin/scenarios/
    └── test_*.py          # AutoSpan executable scenarios
```

AutoSpan collects only contributed scenario roots. `autospan test` refuses to fall back to arbitrary pytest discovery when no scenario root is contributed.

## 14. Definition of a good scenario

A good scenario answers four questions without opening protocol internals:

1. What requirement is being tested?
2. What did we do?
3. What did we observe?
4. Why did it pass or fail?

If answering those requires reading the CAN/ISO-TP implementation, the scenario layer is too low.
