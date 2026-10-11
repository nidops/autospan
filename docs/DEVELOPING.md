# Developing AutoSpan

## Setup

Python 3.11 or newer is required. From this checkout:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

On Windows, create the environment with `py -3 -m venv .venv` and activate it
in PowerShell with `.venv\Scripts\Activate.ps1`, then use the same `python` commands.

If several local packages share a workspace, use one environment at the parent
level and install the core before packages that depend on it. No workspace build
system is required.

## Checks

```bash
python -m pytest -q
python -m ruff check src tests scripts
python -m ruff format --check src tests scripts
python -m mypy src
python -m compileall -q src tests scripts
python scripts/check_readme.py
```

Use `python -m ruff format src tests scripts` to apply formatting. The repository uses a
`src/` layout; editable installation also exposes its command entry point.

These checks cover orchestration, plugin compatibility, resource ownership and
cleanup, gates, frontends, hosted pytest, evidence, and core independence from
project and vendor drivers. The README checker renders the PyPI description and
checks local documentation links and anchors.

For focused console and UI testing:

```bash
python -m pytest -q tests/test_console.py tests/test_console_hardening.py tests/test_tui.py
```

These tests cover step/manifest verdicts, preserved causes, redaction, concurrent
command rejection, shutdown order, UI responsiveness, native command selection,
Unicode/bounded output, log search/copy/export, and script/REPL parity. Headless UI
tests use signals and bounded polling rather than fixed completion sleeps.

## Distribution checks

Build into an empty `dist/` directory; move any previous output aside first.
The build backend creates an sdist and builds the wheel from it.

```bash
python -m build
python -m twine check --strict dist/*.whl dist/*.tar.gz
python scripts/check_dist.py dist
python scripts/verify_install.py dist --distribution sdist
python scripts/verify_install.py dist
```

The distribution checker verifies versions, license, metadata, README, project
links, contributor documentation, package files, and matching wheel/sdist bytes.
The installation verifier creates a temporary isolated environment outside the
checkout, checks the public API and console entry point without optional
dependencies, then installs the test/TUI extras. It checks metadata, scaffolds a
smoke plugin, runs its hardware-free scenario and unit tests, checks an intentional
failure's exit code and evidence manifest, and runs the full packaged core suite
against the installed library.

A core-only environment has no external project or provider entry points. Testing
fakes are importable helpers, not installed extensions.

CI runs quality checks, builds one wheel/sdist pair, tests the sdist installation,
and verifies the installed wheel on CPython 3.11–3.14 on Linux, Windows, and macOS.
See [releasing](../RELEASING.md) for publication using those artifacts.

## Ownership

Keep protocol implementations and project/vendor dependencies outside the core.
Driver selection and composition happen in project code. See
[architecture](ARCHITECTURE.md) and [plugin authoring](PLUGIN_AUTHORING.md).

Consumer and hardware acceptance belong to external plugins and their physical
benches. Physical resource recovery, native traffic capture and real terminal
clipboard behavior require operator testing; local Linux checks do not replace
Windows or macOS CI.
