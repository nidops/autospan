# Developing AutoSpan

## Setup

Python 3.11 or newer is required. From this checkout:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

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
python -m build
```

Use `python -m ruff format src tests scripts` to apply formatting. The repository uses a
`src/` layout; editable installation also exposes its command entry point.

## Distribution smoke

Install the built wheel with its dependencies in a clean environment. From outside
the source tree, run:

```bash
autospan plugins
autospan providers
autospan new sample
python -m pip install -e './autospan-sample[dev]'
autospan test --config autospan-sample/autospan.yaml -- -q
```

A core-only environment has no external project or provider entry points. Testing
fakes are importable helpers, not installed extensions. Inspect the source distribution
as well: it must contain the README, license and linked contributor documentation.

## Ownership

Keep protocol implementations and project/vendor dependencies outside the core.
Driver selection and composition happen in project code. See
[architecture](ARCHITECTURE.md) and [plugin authoring](PLUGIN_AUTHORING.md).
