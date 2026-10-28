# Evidence

AutoSpan evidence is protocol-neutral.

## Model

Each record/artifact can be correlated by:

- `run_id`
- `test_id`
- `step_uid` (unique within the run) and `parent_step_uid`
- `step_id` (legacy human-readable label; not a unique identity)
- `source`

Structured events are stored in `events.jsonl`. Opaque artifacts are listed in `manifest.json` with size, SHA-256, media type, format, completion state, and correlation context.

New runs use `schema_version: 2` in events and the manifest. Existing fields retain
their meanings. `step_id` remains a label for compatibility; readers should join
steps by `(run_id, step_uid)` and use `parent_step_uid` for nesting. Runs without a
schema version use the legacy format and cannot reliably distinguish repeated
step labels. Strict readers must accept the new fields before consuming version 2.

Every event has a run-local `sequence` assigned under the writer lock. Every
`step()` has a run-local `step_sequence` and a unique `step_uid`. These order
observed allocations/writes, not hardware operations across independent threads.
Step completion includes `duration_s` and outcome. Captured contexts and artifacts
carry the same IDs. Explicit legacy `scope(step_id=...)` labels do not manufacture
unique steps; use `step()` when an operation needs an identity.

## Native artifacts

Examples include:

- native binary captures
- packet captures
- raw/text stream captures
- debugger logs/backtraces
- memory dumps
- CSV measurements
- vendor-specific binary output

AutoSpan does not parse these formats merely to store them.

## Data plane rule

Do not route every traffic packet through AutoSpan's generic event stream. Let the owning driver/project recorder record the native stream and commit the resulting file as evidence.

## Steps

Scenarios may use:

```python
with runtime.evidence.step("Reset target"):
    project.reset()
```

Artifacts/events emitted on the owning thread inherit that step context.

Nested and repeated labels receive distinct IDs. Artifact step directories use
the unique ID, so long labels never become step-directory names. Sanitized or
truncated legacy/test path names include a hash to avoid collisions and remain
bounded in bytes. Consumers should follow manifest paths rather than constructing
paths from labels.

## Worker threads

Capture `runtime.evidence.current_context` and bind it with `bind_context()` when
a worker belongs to that test or step. Python context variables do not automatically
propagate to new threads.

A long-lived receiver cannot infer which test or step caused unsolicited traffic.
Record that traffic with run/session context instead. Binding a startup step to a
worker that outlives the step would misattribute later records. Publish completed
operations from their calling thread when caller correlation is available.

## Producer patterns

When a recorder can write to a path supplied by AutoSpan, prefer `reserve_artifact()` so
large captures do not need a second copy:

```python
with runtime.evidence.reserve_artifact(
    kind="traffic",
    source="transport.main",
    suffix=".capture",
    format="native",
    name="traffic",
) as artifact:
    recorder.write_to(artifact.path)
```

When a tool already produced a file elsewhere, use `attach_file()`. Both paths preserve
the file as opaque evidence and add correlation, completion state, size and SHA-256.

Close file handles and join producer workers inside the reservation block, before
it exits. Successful exit commits with `complete=True`; an exception commits the
available file with `complete=False`. A reservation with no file records
`artifact_missing`. Hashing or finalization errors propagate and fail the run.
Reservations must finish before the owning evidence run closes. Manifest paths
are relative to the run directory.

Readable names are collision-safe even when multiple worker threads commit artifacts at
the same time. Artifact suffixes are validated so a producer cannot escape the evidence
directory through a crafted path.

## Finalization and recovery

Explicit run IDs must be bounded portable directory tokens, not paths.
Artifact suffixes accept at most 32 ASCII letters, digits, dots, underscores or
hyphens, beginning with a dot. Manifest paths use forward slashes on every platform.

Finish reservations and worker scopes before closing the store. Invalid manifest
metadata or a failed staging write leaves the run open for correction. After the
finish event is written, the run is sealed. If closing the stream or atomically
replacing the manifest fails, retry `close()` on the same store; it publishes the
original verdict, metadata and artifacts without another finish event. New events
and reservations are rejected after sealing. A process crash has no automatic
resume path: retain the events, artifacts and staged manifest for diagnosis;
a missing manifest is an incomplete run, never a successful one.

Event write failures attempt to truncate the partial record. If that repair fails,
the store refuses successful finalization. Atomic replacement prevents partial
manifest visibility; it is not a guarantee against storage failure or power loss.
Producers must sanitize event attributes and opaque files before export; the store
does not infer secrets. See [console argument privacy](CONSOLE.md#keyboard-and-logs).
