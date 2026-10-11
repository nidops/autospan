# Publication checklist

Follow [VALIDATION.md](VALIDATION.md) and [RELEASING.md](RELEASING.md).
No upload should occur before the reviewed release commit and tag pass CI.

## Technical readiness

- [ ] Build exactly one wheel and one sdist; verify both distributions and the README.
- [ ] Verify clean installed wheel and sdist, CLI entry point, plugin scaffolding and tests.
- [ ] Confirm Linux, Windows and macOS CI on every supported Python version.
- [ ] Confirm metadata version matches the immutable release tag.
- [ ] Configure the `testpypi` and `pypi` GitHub environments and OIDC trusted publishers.
- [ ] Verify the package name belongs to the intended maintainer before publishing.
- [ ] Check both index accounts and existing releases; a missing project page does not prove name availability.
- [ ] Publish the exact validated artifacts to TestPyPI and then PyPI, verifying hashes.

## Publication and security review

- [ ] Confirm the right to publish all source, fixtures and documentation under MIT.
- [ ] Review the source tree and Git history for private material and credentials.
- [ ] Keep protocol implementations, vendor drivers, customer settings and secrets outside core.
- [ ] Review dependency licenses and include required notices.
- [ ] Confirm package metadata and documentation accurately match behavior.
- [ ] Make linked GitHub manuals accessible to anonymous readers with maintainer approval.
- [ ] Verify private security-advisory reporting is enabled and reachable.

Publishing is an explicit maintainer action, never a side effect of a push or
pull request. Do not reuse package versions or conceal an artifact mismatch.
