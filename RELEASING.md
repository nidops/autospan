# Releasing AutoSpan

Package version lives in `pyproject.toml`; `autospan.__version__` reads installed
metadata. The plugin API version is separate and is currently `1`.

## Setup

Configure [trusted publishing](https://docs.pypi.org/trusted-publishers/)
separately on TestPyPI and PyPI for project `autospan`, repository
`nidops/autospan`, and workflow `publish.yml`. Use GitHub environments `testpypi`
and `pypi` respectively, restricted to release tags, with production approval
where available. For a new project, register a pending publisher; this does not
reserve the package name. Confirm account ownership and public documentation links.

## Release

1. Update the package version and documentation.
2. Run the [development checks](docs/DEVELOPING.md#checks) and
   [distribution checks](docs/DEVELOPING.md#distribution-checks).
3. From a clean checkout, tag the reviewed `master` commit as `v<version>` after all
   supported CI jobs pass (for example, `v1.0.0`). Verify the version with
   `python scripts/check_dist.py dist --tag v1.0.0`, substituting the actual version.
4. Push the tag, then manually run **Publish** in GitHub Actions with that tag selected.

The workflow reruns CI, uploads verified artifacts to TestPyPI, checks the downloaded
wheel's hash and installation, then publishes the same artifacts to PyPI and verifies
again. Pushes alone do not publish. Check the package page after completion.

If publication fails, rerun failed jobs in the same run while its `release-dist`
artifact is available. Never overwrite tags or uploaded files, or hide mismatches
with `skip-existing`. Changed release contents require a new version.
