# Validation

## Developer checks

From an isolated environment in this checkout:

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
python -m ruff check src tests scripts
python -m ruff format --check src tests scripts
python -m mypy src
python -m compileall -q src tests scripts
python scripts/check_readme.py
```

These checks cover generic orchestration, plugin compatibility, ownership and
cleanup, gates, frontends, hosted pytest, evidence, and the independence of the
core from project and vendor drivers. They are not a substitute for hardware
tests owned by external plugins.

## Distribution checks

Create a clean `dist/` directory before each build. The build backend creates
an sdist and builds the wheel from it.

```bash
python -m build
python -m twine check --strict dist/*.whl dist/*.tar.gz
python scripts/check_dist.py dist
python scripts/verify_install.py dist --distribution sdist
python scripts/verify_install.py dist
```

The distribution checker verifies version, license, metadata, README, project
links, package files, and wheel/sdist consistency. The installation verifier
creates a temporary isolated environment, installs the wheel or sdist, checks
the public API and console entry point without optional dependencies, then adds
the test/TUI extras, checks metadata, scaffolds a smoke plugin and runs its hardware-free
scenario and plugin unit tests, verifies an intentional failure's exit code and
manifest, then runs the packaged core tests without importing the checkout.

The CI pipeline runs quality checks, builds one pair of distributions, tests
the sdist and verifies wheel installation across CPython 3.11–3.14 on Linux,
Windows, and macOS. The publication workflow reuses those exact artifacts.
See [RELEASING.md](RELEASING.md) for publishing.

## Console hardening acceptance

```bash
python -m pytest -q tests/test_console.py tests/test_console_hardening.py tests/test_tui.py
```

These tests inspect real step/manifest verdicts, preserved causes, redaction,
single-flight rejection, shutdown order, UI responsiveness, native command
selection, Unicode/bounded output, log search/copy/export, and script/REPL parity.
Headless UI tests use signals and bounded polling, not fixed completion sleeps.
The full suite is also included in installed wheel/sdist verification.

Consumer and hardware acceptance belong to the external plugin's own release
checks. Physical resource recovery, native traffic capture and real terminal
clipboard behavior require operator acceptance. Linux results do not substitute
for Windows CI on the exact commit being reviewed.
