# Console

The plain REPL, Textual UI and batch modes share one `CommandShell` and one
runtime. Project commands reuse its live components and evidence.

## Start in 60 seconds

From the AutoSpan source checkout (after publication, use the PyPI package with
the `test,tui` extras instead):

```bash
python -m pip install -e '.[test,tui]'
autospan new sample
python -m pip install -e ./autospan-sample
autospan console --config autospan-sample/autospan.yaml --plain --exec "sample status"
autospan console --config autospan-sample/autospan.yaml
```

The sample needs no hardware. The last command uses Textual on an interactive
terminal when the optional `tui` extra is installed; otherwise it uses the REPL.

| Mode | Invocation | Behavior |
|---|---|---|
| Interactive UI | `autospan console --config autospan.yaml` | Textual when available |
| Plain REPL | add `--plain` | Line input; EOF leaves |
| Batch commands | add repeatable `--exec "project status"` | Always noninteractive |
| Script | add `--script smoke.txt` | UTF-8; blank/full-line comments skipped |
| Continue on error | add `--keep-going` | Batch retains failure while continuing |

An empty script exits successfully without requesting input. Batch execution
stops on the first failure by default; `exit`/`quit` stops any mode.
An interrupt/cancellation (status 130) stops a batch even with `--keep-going`.

## Contribute a command

Plugin API **1** uses `handler(ctx: CommandContext, args: tuple[str, ...])`.
This complete command definition works against any live runtime; register
`STATUS` inside the plugin's `register()` method.

```python
from autospan.api import CommandContext, CommandContribution

def status(ctx: CommandContext, args: tuple[str, ...]) -> int:
    if args:
        raise ValueError("takes no arguments")
    ctx.write(", ".join(ctx.runtime.names()) or "no components")
    return 0

STATUS = CommandContribution(("project", "status"), status, "list live components")
```

Use `ctx.runtime.require(...)` for project facades and `ctx.write(text)` for
line output. Output streams to the terminal or a bounded, thread-safe UI channel.
`print()` is not a command output channel. `execute(capture=True)` captures
explicit writes only, retaining the last 64K characters and a truncation marker;
its `CommandOutcome` also returns the status, exit intent, diagnostic and original
error cause. Do not persist the cause's raw representation.

The initial plugin contract is API 1; declare the literal `api_version=1`.
Unsupported versions fail before planning/acquisition. For unpublished plugins,
replace bare runtime access with `ctx.runtime` and command `print()` with
`ctx.write()` before setting that literal. See [plugin authoring](PLUGIN_AUTHORING.md).

## Parsing and completion

Command paths may be nested. The longest registered path wins. Built-in roots
`help`, `components`, `evidence`, `exit` and `quit` are reserved; duplicates are
rejected before acquisition. `help [COMMAND]` shows descriptions and usage.

Quote arguments containing spaces. Backslashes are literal on all platforms:
`project open "C:\Program Files\records.txt"` retains the Windows path.
Tab accepts unfinished quotes and replaces the token at the cursor while
preserving subsequent text. Custom `complete(runtime, completed_args)` functions
return decoded candidates; suggestions are sorted/deduplicated. Keep completers
fast and side-effect free. Argument completers do not run during an active command.

## Stop safely

Only one command runs at a time. Busy submissions are rejected rather than
queued; the UI remains available for navigation/search while a command runs.
States are `idle / running / stopping / failed`, with operation, last status,
components and evidence directory shown alongside the log.

Ctrl+C requests cooperative cancellation in Textual. Handlers can use
`ctx.cancel_requested`, `ctx.wait(seconds)` or `ctx.check_cancelled()`.
A handler must finish stopping its driver/context managers before acknowledging
cancellation. An acknowledged cancellation returns status 130.

Ctrl+Q requests cancellation and quits **after the command thread finishes**.
Forced UI exit also joins the worker before runtime teardown. A driver that
cannot interrupt an operation is allowed to finish; there is no timeout that
abandons a live hardware worker. Such an operation can finish successfully after
a stop request. In the plain REPL, Ctrl+C during a handler returns 130 after its
stack unwinds; Ctrl+C at an idle prompt leaves the session.

Handlers must join their own child workers and must not start another test run,
runtime session or hardware owner. Runtime periodic workers remain owned by the
canonical session.

## Keyboard and logs

- Enter submits; Up/Down recalls command paths; Tab completes.
- Ctrl+P opens Textual's fuzzy plugin-command picker. Selection fills the prompt.
- Ctrl+F searches retained log lines; Enter filters, an empty search restores them.
- Ctrl+Y copies retained text using the terminal clipboard protocol.
- Ctrl+S explicitly exports UTF-8 `console.log` in the evidence directory; use this
  when the terminal does not support clipboard transfer.

The output queue is bounded to 128 chunks of at most 2048 characters and applies
backpressure. Rendering drains a limited batch on the UI thread. Logs retain
1000 lines (at most 4096 characters each); history retains 100 paths.
Arguments are excluded from automatic history, command echoes and evidence
labels. Error diagnostics replace exact argument values, including values after
`=`. This does not redact secrets obtained from configuration, environment variables
or devices, nor encoded/transformed arguments. Plugins must keep those values out
of exceptions and evidence. `ctx.write()` can deliberately expose any content;
clipboard/export intentionally includes that output.
Plain-mode Tab/history requires `readline`; it is unavailable in standard
Windows Python. Textual supplies those features on Windows.

## Gates, errors and evidence

No hardware gate is implicit. Add `--hardware`, `--destructive` (implies hardware),
or repeatable `--gate NAME` for configured components. Missing component gates
reject acquisition before physical enumeration; commands do not create another
gate mechanism.

Every **invoked contributed command** records one step named `console: COMMAND PATH`.
Status zero passes; nonzero returns, cancellations and caught exceptions fail.
Nested events/artifacts retain the step's correlation. Built-ins, invalid input
and busy rejections do not create command steps.

Any executed failure makes the overall session fail in every mode, even after
later success or `quit`. Batch returns a nonzero status on failure.
Expected input errors include usage; unexpected errors report their type and
sanitized message without an operator traceback. Startup, teardown and evidence
failures remain failures and are surfaced. Evidence lives under
`artifacts/evidence/<run-id>/`; TUI clipboard exports are operator copies,
not automatically registered evidence artifacts.

Keyboard behavior is covered by headless UI tests. Clipboard transfer still
depends on the physical terminal and requires operator verification.
