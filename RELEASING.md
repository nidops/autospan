# Releasing AutoSpan

The first package release is `0.1.0`, with plugin API 1. Package version lives
only in `pyproject.toml`; `autospan.__version__` reads installed metadata.
Unpublished development revisions are not releases.

## One-time setup

Create GitHub environments `testpypi` and `pypi`, restrict them to reviewed
`vX.Y.Z` tags, and require production approval where repository settings allow it.
Register separate pending trusted publishers on [TestPyPI](https://test.pypi.org/manage/account/publishing/)
and [PyPI](https://pypi.org/manage/account/publishing/):

| Field | TestPyPI | PyPI |
|---|---|---|
| Project | `autospan` | `autospan` |
| Owner / repository | `nidops` / `autospan` | `nidops` / `autospan` |
| Workflow | `publish.yml` | `publish.yml` |
| Environment | `testpypi` | `pypi` |

A pending publisher does not reserve a name. Confirm ownership before uploading.
See [PyPI's setup instructions](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/).
Publishing uses short-lived OIDC credentials.

## Publish a reviewed commit

1. Complete [VALIDATION.md](VALIDATION.md) and [PUBLICATION_CHECKLIST.md](PUBLICATION_CHECKLIST.md).
2. Confirm the exact PR head's Linux, Windows and macOS CI results for Python
   3.11–3.14. Obtain maintainer approval, merge normally, and require fresh CI
   plus a clean-install smoke on the merged `master` commit.
3. Obtain explicit maintainer authorization before changing repository visibility,
   configuring publishers, tagging or publishing. Create the immutable `v0.1.0`
   tag at the approved merged commit; never overwrite an existing release tag.
4. Run the **Publish** workflow manually, selecting that tag.

The workflow repeats CI, builds one wheel/sdist pair, verifies metadata against
the tag, uploads the tested artifacts to TestPyPI, downloads and checks the wheel
hash and installation, then promotes the same files to PyPI and verifies again.
Pushes and pull requests do not publish.

If an upload job fails, retry the failed jobs while that run's `release-dist`
artifact remains available. Once files have been uploaded, never replace their
version with rebuilt files; change the package version for changed release bits.

## Local artifact verification

Build into an empty directory:

```bash
python -m build
python -m twine check --strict dist/*.whl dist/*.tar.gz
python scripts/check_dist.py dist --tag v0.1.0
python scripts/verify_install.py dist --distribution sdist
python scripts/verify_install.py dist
```

Check the public description, metadata and clean installation after publication.
Hardware acceptance remains with external plugins and their physical benches.
