# Evidence

AutoSpan evidence is protocol-neutral.

## Model

Each record/artifact can be correlated by:

- `run_id`
- `test_id`
- `step_id`
- `source`

Structured events are stored in `events.jsonl`. Opaque artifacts are listed in `manifest.json` with size, SHA-256, media type, format, completion state, and correlation context.

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
