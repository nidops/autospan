# Reliability and compatibility changes

The 0.2.0 reliability changes preserve one runtime lifecycle and process-local
resource claims. They do not add host-wide or distributed resource locking.

## Required verification in CI

Hardware authorization and verification expectations are separate:

```bash
autospan test --config bench.yaml -- --hardware --require-gate hardware -q
```

`--hardware` permits hardware access. `--require-gate hardware` requires at least
one qualifying test to pass setup, call and teardown. It never enables a gate.
Repeat `--require-gate` to require coverage for several gates. Qualifying tests
carry that gate's marker (including implied gates), or use the `runtime`/
`session_runtime` fixtures for a plan containing components with that gate.
For dynamically requested shared fixtures, mark the tests explicitly.

Skips, deselected tests, setup-only work, expected failures and non-strict unexpected
passes do not count. A passing software test cannot satisfy hardware coverage.
The requirement means at least one passing test **per gate**, not every test in
a mandatory acceptance suite. Projects still define and select that suite.

Without this option, optional hardware and ordinary skips keep normal pytest
semantics. Missing authorization or unknown gate names produce usage errors
(exit 4). Missing passing coverage changes an otherwise successful run to exit 1.
Existing test/collection failures, interrupts and no-tests exit 5 are preserved.
The normal `autospan test` frontend returns that status and records the run outcome.
`required_gates_checked` records passing counts and unmet gates in evidence.

Required-gate accounting currently supports serial pytest only. Combining it with
xdist workers produces an explicit usage error; worker aggregation and cross-process
runtime ownership are not claimed. Run required verification in a serial job.

## Lazy provider ownership

First resolution is serialized. Acquisition and contract validation happen in a
temporary ownership scope. Invalid acquisitions close before resolution raises;
only a valid instance transfers to runtime cleanup. A successful instance closes
once when its owner exits. Resolution after owner exit is rejected, including a
provider that was never opened. Drivers already returned to callers remain subject
to the documented runtime lifetime; references cannot revoke arbitrary driver use.

A failed attempt can be explicitly retried after successful cleanup; there is no
automatic retry. If rollback itself fails, the original failure retains the cleanup
error as a note and that lazy provider refuses further acquisitions. Factories still
own cleanup for partial startup that fails before their context manager yields.
Shutdown waits for an in-progress opener rather than abandoning its thread.

## Conformance helper migration

All checks now remain active under `python -O`.

`check_flasher()` requires `allow_flash=True` before invoking the driver. Supply a
`FlashRequest`, not a path as the request itself. Use a fake or an independently
approved target; the boolean is explicit caller intent, not a hardware safety gate.

```python
from autospan.capabilities.flasher import FlashRequest
from autospan.capabilities.flasher.fake import FakeFlasher
from autospan.testing.conformance import check_flasher

result = check_flasher(FakeFlasher(), FlashRequest(), allow_flash=True)
assert result.ok
```

`check_debugger(driver)` now checks protocol shape without executing target
operations. The former unconditional run/halt and write to `x` are removed.
To exercise a fake or explicitly approved scratch target, use
`check_debugger(driver, allow_control=True, scratch_variable="approved_scratch")`.
This invasive mode leaves the target halted and restores the scratch value even
if its round-trip check fails. Restoration failures are surfaced, not claimed safe.
Callers still own acquisition, authorization and cleanup.

## Other compatibility points

Existing project requirements such as `autospan>=0.1,<0.2` exclude this release.
After compatibility review, widen those bounds to include `0.2.0`. New scaffolds
require `autospan>=0.2,<0.3`; the generated plugin's own initial version stays `0.1.0`.

- [Configuration](CONFIGURATION.md): duplicate explicit YAML keys and accidental
  string name collections now fail early instead of silently changing meaning.
- [Evidence](EVIDENCE.md): schema 2 adds unique step/parent IDs and event ordering;
  legacy step labels remain available. Strict readers need additive-field support.
- [Console](CONSOLE.md): commands may explicitly declare sensitive argument positions;
  unannotated commands retain conservative error masking and argument-free history.
- CLI AutoSpan error diagnostics now use stderr and retain exit 2. Plugin API mismatch
  diagnostics recommend compatible versions without prescribing an unrelated migration.
- Plugin API remains 1. Resource claims remain process-local, and periodic failure
  reporting retains its documented guarantees.
- A failed resource reacquisition invalidates component access through the runtime
  view until the owning session is torn down. Already-held raw driver references
  cannot be revoked by a Python facade.
- Hosted test selection must remain within contributed scenario paths.
