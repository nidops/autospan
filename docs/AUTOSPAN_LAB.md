# AutoSpan Lab

**A live, hands-on tour of the AutoSpan console. No hardware required.**

> **Simulation only.** This is an isolated console playground. It does not load
> project plugins, acquire physical resources, validate real drivers, or produce
> real AutoSpan bench-evidence runs. Use it to learn the shell and operator UX.

## Start in one minute

Install the published package with the console extra:

```bash
python -m pip install 'autospan[tui]==0.2.0'
python -m autospan.demos --check
python -m autospan.demos
```

No YAML to create. No accounts, network services, hardware, or plugin needed.

If you have only the base installation (no Textual), explore in plain mode:

```bash
python -m autospan.demos --plain
```

Run exactly one command without opening the UI:

```bash
python -m autospan.demos --exec 'lab run smoke'
```

The Lab entry point is `python -m autospan.demos`.

## Your first 60 seconds

Type these commands in the interactive prompt:

```text
lab tour
lab devices
lab select sensor-a
lab read temperature
lab run smoke
```

You should see three simulated devices, a reading, and a successful scenario.
The sample values are invented, stable virtual data, not real measurements.

Then type:

```text
lab run endurance
```

While it runs, press **Ctrl+C**. The command checks for cancellation, unwinds
its `finally` cleanup, and the console waits for its worker to finish.
Cancellation must not abandon a worker that might own hardware in a real
project. This demo never owns hardware, but it uses the same shell/cancellation
entry points rather than manufacturing a second thread.

## Explore commands instead of memorizing them

The Textual UI supports two distinct modes of discovery. These shortcuts also
apply to the regular project console. Plain mode uses a simple line prompt.

| Key | Meaning |
| --- | --- |
| **Ctrl+Space** (or **Ctrl+@**) | Show suggestions for the cursor position |
| **Ctrl+P** | Search all commands globally |
| **Tab / Shift+Tab** | Complete/cycle a token; with suggestions open, Tab inserts and Shift+Tab moves up |
| **↑ / ↓** (while suggestions are open) | Highlight a suggestion |
| **Enter** (while suggestions are open) | Insert the suggestion; **do not execute** |
| **Esc** | Dismiss suggestions without changing input |
| **Ctrl+C** | Clear idle input and suggestions, or request cancellation of the active command |
| **Ctrl+F / Ctrl+G** | Search output / return to latest output |
| **Ctrl+B / Ctrl+Q** | Toggle sidebar / exit after active work finishes |

Suggestions come from AutoSpan's `CommandShell.completion()` and the registered
`CommandContribution` objects, not a hardcoded command list in the UI.

Try this sequence:

```text
lab                  [Ctrl+Space]  # tour, devices, select, read, ...
lab select           [Ctrl+Space]  # controller, sensor-a, power
lab read             [Ctrl+Space]  # current device's parameters
lab set              [Ctrl+Space]  # current device's writable keys
lab run              [Ctrl+Space]  # smoke, diagnostic, endurance, failure
```

Change the selected device and repeat `lab read` to see suggestions follow
state. Editing an open suggestion query refreshes results. Moving the cursor,
opening log search, or opening the command palette dismisses suggestions.
Selection inserts text; press Enter again to execute it. The UI renders no
more than 50 suggestions. Completers may still enumerate all values, so keep
large plugin catalogs bounded in the provider.

## Discover the virtual bench

| Device | Readable values | Writable values |
| --- | --- | --- |
| `controller` | `firmware`, `mode`, `uptime` | `mode`: `standby`, `active` |
| `sensor-a` | `temperature`, `humidity`, `sample_rate` | `sample_rate`: 1, 5, 10, 20, 50 Hz |
| `power` | `voltage`, `current`, `relay` | `relay`: `off`, `on` |

Use `lab devices` to list these simulated devices. The built-in `components`
command lists no live components because the Lab acquires none.

For a writable setting, first select its device:

```text
lab select power
lab set relay on
lab read relay
lab run smoke
lab read relay
```

The smoke scenario restores any virtual relay changes it makes. No physical
signal is switched. Values changed directly by `lab set` remain set within the
same Lab shell session until you exit.

## Failure, streaming, and cancellation

- `lab run failure` deliberately reports a **synthetic failure**, returns a
  nonzero command status, and still runs simulated cleanup.
- `lab stream` emits sixty seeded telemetry lines over several seconds through
  the normal line-oriented `ctx.write()` API and console output queue.
- `python -m autospan.demos --developer` exposes `lab stubborn`, a deliberately
  non-cooperative but **bounded** two-second operation. It tests the important
  fact that the console waits for the worker instead of killing it.

Do not describe these synthetic checks as real hardware qualification.

## Output and session lifetime

Normally the Lab's log-export directory is temporary. It is deleted when the
Lab exits. To preserve an explicitly exported `console.log` (`Ctrl+S`):

```bash
python -m autospan.demos --keep-output ./lab-output
```

`console.log` is a UI transcript, **not** a substitute for the real AutoSpan
`events.jsonl`/`manifest.json` evidence produced by project runs. For genuine
plan/resource/evidence demonstrations, use the installed sample project or a
real project plugin and the normal `plan`, `test`, and `console` frontends.

## Troubleshooting

**No interface opens:** use `--check` first. Textual requires
`python -m pip install 'autospan[tui]==0.2.0'` in your active environment and an
interactive terminal. Use `--plain` on headless systems.

**Ctrl+Space does nothing:** some terminal emulators send `Ctrl+@` instead.
Try `Tab` or `Ctrl+P` for their different purposes. Terminal shortcut
interception varies by emulator.

**Where did the output go?** `Ctrl+S` exports retained UI logs to the active
Lab output directory. Use `--keep-output` if you want those logs after exit.

**Exit code is nonzero after an intentional failure or cancellation:** that is
expected. The console retains a failed/cancelled status for the session.
Use a fresh run to demonstrate a clean success.

## Move from simulation to a real project

The Lab proves the *human interaction* model, not the full resource lifecycle.
For component planning, gates, ownership, teardown, and authentic evidence,
continue with the [sample project](CONSOLE.md#start-in-60-seconds) and
[plugin-authoring guide](PLUGIN_AUTHORING.md).
