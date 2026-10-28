# Changelog

## 0.2.0

Updates the published predecessor `0.1.0.post1`; plugin API remains 1.

- Generated projects now require `autospan>=0.2,<0.3`, avoiding a downgrade
  during the quick start. Existing project bounds excluding 0.2 need review.

- Close invalid lazy provider acquisitions immediately; serialize resolution and
  reject use after owner teardown or failed rollback.
- Keep conformance checks active under optimized Python; require explicit flashing
  consent and make debugger conformance non-invasive by default.
- Reject duplicate explicit YAML keys and malformed name collections.
- Add unique evidence step/parent IDs and ordered events in additive schema 2.
- Add declared command argument privacy and preserve unrelated numeric diagnostics.
- Add opt-in required-gate coverage for serial hosted pytest.
- Send CLI errors to stderr and clarify API mismatch diagnostics and package installation.
- Reject hosted test selections outside contributed packages and paths escaping scenario roots.
- Invalidate runtime access when a handed-over resource cannot be reacquired.
- Make evidence finalization retryable after invalid metadata, interrupted stream closing,
  and publication errors; retries preserve the finished verdict and metadata.
- Validate parsed pytest options, including response files and attached option values,
  before scenario collection. Framework-owned configuration cannot be overridden.
- Reject delayed handovers from earlier runtime activations and distributed required-gate modes.

See [reliability and migration notes](docs/RELIABILITY.md) for API behavior changes.
