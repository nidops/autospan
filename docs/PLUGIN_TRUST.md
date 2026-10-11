# Plugin Trust and Security Model

AutoSpan plugins are **trusted Python code**, not sandboxed extensions.

## Installed, selected, activated

- **Installed**: a distribution exists in the Python environment.
- **Selected**: its plugin name appears in the active AutoSpan configuration.
- **Activated**: the selected plugin is imported/registered and its runtime components may later be opened.

Merely installing an external plugin does not make AutoSpan import it. Selecting it does.

## Trust boundary

A selected plugin can execute arbitrary Python during import/registration and later during component lifecycle. AutoSpan does not attempt process isolation, permission filtering, or sandboxing.

Production environments SHOULD:

- pin approved plugin versions;
- review/approve plugin distributions before installation;
- use isolated virtual environments;
- avoid installing unrelated untrusted pytest/plugin packages on production benches;
- keep private project plugins and credentials/configuration out of public repositories.

AutoSpan disables third-party pytest-plugin autoloading during hosted scenario execution so unrelated installed pytest extensions do not silently alter the test runtime. This is execution isolation, **not** a security sandbox.
