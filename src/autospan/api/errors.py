"""Stable AutoSpan exception hierarchy."""


class AutoSpanError(RuntimeError):
    """Base class for public AutoSpan failures."""


class PluginError(AutoSpanError):
    """Plugin discovery, registration, or compatibility failure."""


class PlanError(AutoSpanError):
    """Runtime-plan validation failure."""


class LifecycleError(AutoSpanError):
    """Runtime startup or teardown failure."""


class ConfigurationError(AutoSpanError):
    """Configuration is malformed or incomplete."""


class GateError(AutoSpanError):
    """A runtime component requires an execution gate the frontend has not enabled."""

    def __init__(self, message: str, *, missing: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.missing = missing
