# Configuration

AutoSpan owns only the host section. Each selected project plugin owns and strictly validates its own section.

Duplicate explicit YAML keys are errors, including nested mappings and `--set`
values. Diagnostics identify the source location and key. YAML anchors and merge
keys remain supported: an explicit key may override an inherited key, and earlier
maps in a merge sequence take precedence. Recursive merges are rejected. Loading
uses safe YAML constructors and never executes Python objects.

```yaml
autospan:
  plugins: [my-project]

my-project:
  port: sim
```

`autospan` currently accepts only `plugins` and `pytest`. Unknown host keys are errors. Project sections are validated by their owning project package; unknown project keys should likewise fail rather than silently default.

Project plugins are loaded only when explicitly named. Installing a driver, provider or another project package does not activate it.

## Command-line overrides

Every config-aware command accepts repeatable `--set dotted.key=value`. Values are parsed as YAML and applied before project loading:

```bash
autospan test --config bench.yaml --set my-project.bench_config=C:/benches/b12.yaml
autospan plan --config bench.yaml --set autospan.plugins=[my-project]
```

## Hosted pytest settings

`autospan test` never reads an arbitrary project `pytest.ini`/`pyproject.toml`. It writes one `pytest.ini` per run from:

1. AutoSpan defaults;
2. `ScenarioContribution.pytest_ini` defaults from selected projects;
3. operator `autospan.pytest.ini` overrides.

Extra pytest plugins must be explicit because autoload is disabled:

```yaml
autospan:
  plugins: [my-project]
  pytest:
    plugins: [pytest_metadata.plugin]
    ini:
      log_cli_level: DEBUG
```

`cache_dir`, `testpaths`, `rootdir` and `confcutdir` are owned by AutoSpan.
Hosted runs reject alternate configuration files, `--pyargs`, and command-line
ini overrides of those keys or `addopts`. Configure `addopts` through the host
settings above. Pytest parses option values normally, including `-k`, `-m`,
logging/report paths and explicit plugin options. `@argument-files` work with
pytest versions that support them.
All positional selections must resolve inside contributed scenario directories;
symlink escapes are rejected before importing test modules.

Planning is side-effect free. Invalid component dependencies, cycles, resource collisions, missing providers, unknown component types and malformed configuration must fail before hardware opens.

Python API collections such as `ComponentSpec.dependencies`, `ComponentSpec.gates`,
`GateContribution.implies`, and `RuntimeSession.enabled_gates` require collections
of names. Use `("hardware",)`, not `"hardware"`. Names must be nonempty strings
without surrounding whitespace. Repeated gates are deduplicated; repeated
dependencies are rejected. Project gate names are validated during host planning.

Relative project paths should be resolved against `PlanBuilder.config_dir`, the directory containing the selected AutoSpan config file.

Keep machine-specific settings in ignored files such as `bench.local.yaml`.
For credentials, store an environment-variable name in reviewed config and resolve
its value at runtime in project code.
