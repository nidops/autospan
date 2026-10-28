# Releasing AutoSpan

Package version lives in `pyproject.toml`; `autospan.__version__` reads installed
metadata. The plugin API version is separate and is currently `1`.

AutoSpan `0.2.0` adds reliability and execution-scope fixes while keeping plugin
API version 1. Review the changelog and compatibility notes before updating.

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
   supported CI jobs pass (for example, `v0.2.0`). Verify the version with
   `python scripts/check_dist.py dist --tag v0.2.0`, substituting the actual version.
4. Push the tag, then manually run **Publish** in GitHub Actions with that tag selected.

The workflow reruns CI, uploads verified artifacts to TestPyPI, checks the downloaded
wheel's hash and installation, then publishes the same artifacts to PyPI and verifies
again. Pushes alone do not publish. Check the package page after completion.

If publication fails, rerun failed jobs in the same run while its `release-dist`
artifact is available. Never overwrite tags or uploaded files, or hide mismatches
with `skip-existing`. Changed release contents require a new version.

Keep the existing project registrations on PyPI and TestPyPI when publishing a
replacement release. Deleting a project or release does not permit reuse of its
uploaded filenames; see [PyPI's filename reuse policy](https://docs.pypi.org/publishing/troubleshooting/).
If older files must be removed, manage the affected release separately from the
project registration. Deletion is irreversible; yanking keeps files available
for exact pins and is not a way to remove their contents.
