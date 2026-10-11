# Security policy

Report vulnerabilities privately to the maintainers (use the repository's private
security advisory feature); do not open a public issue.

AutoSpan plugins are trusted Python code and are not sandboxed (see
`docs/PLUGIN_TRUST.md`). Reports about a malicious or compromised plugin are out
of scope; reports about the kernel itself (path handling in the evidence store,
configuration parsing, plan validation, gate bypass) are in scope.

Supported versions: the latest `0.x` release.