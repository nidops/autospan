# Contributing

Install the development dependencies and run the checks in
[the development guide](docs/DEVELOPING.md).

Keep the core independent of protocols, vendors and projects. Driver implementations
and project configuration belong in separate packages. Add a provider contract only
when real tool substitution requires it and its semantics can stay generic; see
[the design principles](docs/DESIGN.md).

Include tests for behavior changes and update the relevant public docs. Use synthetic
fixtures and contribute only material you have the right to license under MIT.
Keep private configuration, credentials and generated evidence out of commits.
