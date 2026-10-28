# Console

The plain REPL, Textual UI and batch modes share one `CommandShell` and one
runtime. Project commands reuse its live components and evidence.

For a no-config console playground, run `python -m autospan.demos` with the
`tui` extra installed. The [AutoSpan Lab guide](AUTOSPAN_LAB.md) covers this
isolated simulation; it does not acquire project components or record evidence.

## Start in 60 seconds

Install AutoSpan and generate a hardware-free sample:

```bash
python -m pip install 'autospan[test,tui]==0.2.0'
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

Declare the literal `api_version=1`; unsupported versions fail before
planning/acquisition. See [plugin authoring](PLUGIN_AUTHORING.md).

## Parsing and completion

Command paths may be nested. The longest registered path wins. Built-in roots
`help`, `components`, `evidence`, `exit` and `quit` are reserved; duplicates are
rejected before acquisition. `help [COMMAND]` shows descriptions and usage.

Quote arguments containing spaces. Backslashes are literal on all platforms:
`project open "C:\Program Files\records.txt"` retains the Windows path.
Tab accepts unfinished quotes and replaces the token at the cursor while
preserving subsequent text. When multiple candidates match, the first Tab inserts
any shared prefix and shows the choices in the log; further Tab/Shift+Tab presses
cycle forward/backward through candidates without destroying text after the cursor.
Editing the text or moving the cursor resets the cycle. Custom
`complete(runtime, completed_args)` functions return decoded candidates;
suggestions are sorted/deduplicated. Keep completers fast and side-effect free.
Argument completers do not run during an active command.

## Stop safely

Only one command runs at a time. Busy submissions are rejected rather than
queued; the UI remains available for navigation/search while a command runs.
States are `idle / running / stopping / failed`, with operation, last status,
components and evidence directory shown alongside the log.

Ctrl+C requests cooperative cancellation in Textual. Handlers can use
`ctx.cancel_requested`, `ctx.wait(seconds)` or `ctx.check_cancelled()`.
A handler must finish stopping its driver/context managers before acknowledging
cancellation. An acknowledged cancellation returns status 130. The console stays
open for the next command. At an idle Textual prompt, Ctrl+C clears the command
draft, resets history navigation and records `^C` without logging the discarded
input. It also closes log search and restores unfiltered output; Escape closes
search while preserving the command draft. Idle Ctrl+C leaves an open command
palette untouched. While a command is running or its final output is draining,
Ctrl+C preserves the draft. Use Ctrl+Q to quit.

Ctrl+Q requests cancellation and quits **after the command thread finishes**.
Forced UI exit also joins the worker before runtime teardown. A driver that
cannot interrupt an operation is allowed to finish; there is no timeout that
abandons a live hardware worker. Such an operation can finish successfully after
a stop request. In the plain REPL, Ctrl+C during a handler returns 130 after its
stack unwinds. Ctrl+C at an idle plain prompt keeps the session and its acquired
components open. Use `exit`, `quit` or EOF to leave: typically Ctrl+D on Unix, or
Ctrl+Z then Enter in the Windows console.

Handlers must join their own child workers and must not start another test run,
runtime session or hardware owner. Runtime periodic workers remain owned by the
canonical session.

## Keyboard and logs

- Enter submits; Up/Down recalls command paths; Tab completes and cycles matches;
  Shift+Tab cycles backwards.
- Ctrl+P opens Textual's global fuzzy command picker. It includes built-ins and
  plugin commands; selecting one fills the prompt.
- Ctrl+Space (or Ctrl+@, depending on the terminal) shows contextual suggestions
  at the prompt cursor, using the shell's completion providers. An exact command
  path such as `lab read` shows its next arguments without needing a trailing
  space. Up/Down moves the highlight; Enter, Tab, or a mouse click inserts a
  choice without executing. Shift+Tab moves up. Escape closes suggestions and
  preserves the draft; Ctrl+C clears both. Editing refreshes choices, while
  cursor movement or switching to search/the palette closes them. Suggestions
  are unavailable while a worker is running or its output is draining. At most
  50 choices are displayed; providers may still enumerate more candidates.
- Ctrl+F searches retained log lines; Enter filters, an empty search restores them.
  Escape from the search field or command prompt closes search, clears the filter
  and restores retained output. It also works after pressing Enter to apply a search.
- Ctrl+B toggles the details sidebar. On terminals narrower than 90 columns, the
  sidebar automatically hides so the command log remains useful. A manual toggle
  overrides automatic resizing for the remainder of the session.
- Ctrl+G closes search, clears any filter and scrolls to the newest unfiltered
  output, returning focus to the prompt. The command draft is preserved.
- A command submitted while another runs is never queued or discarded: the
  typed input remains ready for explicit submission after the worker finishes.
- The sidebar shows elapsed time for the active or most recent command worker.
  Timing stops when the worker finishes, even while remaining output is rendered.
  It includes dispatch, evidence handling and any output backpressure; it is not
  a measurement of hardware execution time alone.
- Ctrl+Y copies retained text using the terminal clipboard protocol.
- Ctrl+S explicitly exports UTF-8 `console.log` in the evidence directory; use this
  when the terminal does not support clipboard transfer.

The output queue is bounded to 128 chunks of at most 2048 characters and applies
backpressure. Rendering drains a limited batch on the UI thread. Logs retain
1000 lines (at most 4096 characters each); history retains 100 paths.
Output chunks are reassembled into lines before display; CRLF is normalized,
blank lines are preserved, and long lines are split into display segments.
Arguments are excluded from automatic history, command echoes and evidence
labels. By default, error diagnostics conservatively mask arguments, including
values after `=`. Numeric values match complete tokens, so argument `1` does not
change an unrelated `10` in an error. This default does not filter handler output.

Commands can declare `sensitive_args=(1,)` to mark the second decoded argument
sensitive (zero-based, excluding the command path). Only declared positions are
masked in diagnostics, completion errors, and each `ctx.write()` call; public
arguments remain readable. `sensitive_args=()` explicitly declares all arguments
public. The default `None` preserves conservative diagnostics for older plugins.
History and automatic command echoes remain argument-free in every mode.

This is literal-value filtering, not a security sandbox. Numeric values match
tokens; other declared values match literal occurrences. It does not identify
secrets from configuration, environment variables or devices, transformed values,
or values split across writes. Direct evidence events, opaque artifacts and
arbitrary plugin I/O remain the plugin's responsibility. `CommandOutcome.error`
retains the original exception in memory; never serialize its traceback, causes
or notes into public logs. Clipboard/export includes retained console output.
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
