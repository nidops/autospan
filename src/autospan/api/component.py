"""Public component and runtime-plan contracts."""

from __future__ import annotations

import re

from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from autospan.api.periodic import PeriodicScheduler

_GATE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def _freeze(value: Any) -> Any:
    """Recursively freeze configuration values used in an immutable runtime plan."""

    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze(item) for item in value)
    if isinstance(value, frozenset):
        return frozenset(_freeze(item) for item in value)
    return value


class ClaimMode(str, Enum):
    """How a runtime component intends to access a physical/logical resource."""

    SHARED = "shared"
    EXCLUSIVE = "exclusive"


@dataclass(frozen=True, slots=True)
class ResourceClaim:
    """Opaque canonical resource claim produced by a plugin."""

    key: str
    mode: ClaimMode = ClaimMode.EXCLUSIVE

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("ResourceClaim.key must not be empty")


@dataclass(frozen=True, slots=True)
class ComponentSpec:
    """One immutable configured runtime component.

    ``gates`` names execution gates (for example ``"hardware"``) that must be
    enabled by the active frontend before this component may be acquired. A
    frontend that has not enabled a required gate refuses acquisition with
    :class:`~autospan.api.errors.GateError` before any factory is called, so a
    gated factory cannot be called by an ungated run. This does not constrain
    side effects in trusted plugin import/registration code.
    """

    name: str
    type_id: str
    dependencies: tuple[str, ...] = ()
    claims: tuple[ResourceClaim, ...] = ()
    config: Mapping[str, Any] = field(default_factory=dict)
    gates: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("ComponentSpec.name must not be empty")
        if not self.type_id.strip():
            raise ValueError("ComponentSpec.type_id must not be empty")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise ValueError(f"Component {self.name!r} contains duplicate dependencies")
        object.__setattr__(self, "dependencies", tuple(self.dependencies))
        object.__setattr__(self, "claims", tuple(self.claims))
        object.__setattr__(self, "gates", tuple(dict.fromkeys(g.strip() for g in self.gates)))
        invalid = [gate for gate in self.gates if not _GATE_NAME.fullmatch(gate)]
        if invalid:
            raise ValueError(f"Component {self.name!r} declares invalid gate name(s): {invalid}")
        object.__setattr__(self, "config", _freeze(dict(self.config)))


#: Hosted pytest ini keys owned by the framework; plugins/operators cannot set them.
RESERVED_PYTEST_INI_KEYS = frozenset({"cache_dir", "testpaths", "rootdir", "confcutdir"})


def normalize_pytest_ini(values: Mapping[str, Any], *, owner: str) -> Mapping[str, str]:
    """Validate and freeze pytest ini defaults (``log_cli``, ``addopts`` ...)."""

    result: dict[str, str] = {}
    for key, value in values.items():
        name = str(key).strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(f"{owner}: invalid pytest ini key {key!r}")
        if name in RESERVED_PYTEST_INI_KEYS:
            raise ValueError(f"{owner}: pytest ini key {name!r} is owned by AutoSpan")
        if isinstance(value, bool):
            text = "true" if value else "false"
        elif isinstance(value, (list, tuple)):
            separator = " " if name == "addopts" else "\n"
            text = separator.join(str(item) for item in value)
        else:
            text = str(value)
        # ini continuation lines must be indented.
        text = "\n  ".join(line.strip() for line in text.splitlines() if line.strip())
        result[name] = text
    return MappingProxyType(result)


@dataclass(frozen=True, slots=True)
class ScenarioContribution:
    """A package-relative directory containing executable AutoSpan scenarios.

    ``pytest_ini`` holds hosted-pytest ini defaults the scenarios were written
    for (e.g. ``log_cli: true``). Hosted runs never read a project
    ``pytest.ini``, so this is the only way a plugin shapes the pytest session.
    ``addopts`` values from every source are appended; other keys must agree
    across selected plugins, and the operator's ``autospan.pytest.ini`` wins.
    """

    package: str
    relative_path: str = "scenarios"
    pytest_ini: Mapping[str, str] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "pytest_ini",
            normalize_pytest_ini(self.pytest_ini, owner=f"scenarios {self.package!r}"),
        )


@dataclass(frozen=True, slots=True)
class RuntimePlan:
    """Deeply immutable side-effect-free runtime description.

    The plan is safe to inspect, cache, print, or validate without touching
    hardware. Nested mappings/lists supplied by plugins are recursively frozen.
    """

    components: tuple[ComponentSpec, ...]
    scenarios: tuple[ScenarioContribution, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "components", tuple(self.components))
        object.__setattr__(self, "scenarios", tuple(self.scenarios))
        object.__setattr__(self, "metadata", _freeze(dict(self.metadata)))


@runtime_checkable
class ResourceYield(Protocol):
    """A component that can temporarily give up the physical resource it holds.

    Needed when an external tool (a flasher, a vendor application) must own a
    shared channel for a while. Implement both methods idempotently.
    """

    def release(self) -> None:
        """Free the physical resource so another tool may take it."""
        ...

    def reacquire(self) -> None:
        """Take the resource back and restore the component to its working state."""
        ...


class ComponentContext(Protocol):
    """Narrow context visible to a component factory during acquisition."""

    @property
    def evidence(self) -> Any:
        """Evidence sink bound to the runtime currently acquiring this component."""
        ...

    @property
    def periodic(self) -> PeriodicScheduler:
        """Runtime-owned scheduler for generic periodic callbacks."""
        ...

    @property
    def outcome(self) -> str | None:
        """Verdict of the scope owning this runtime, read during component teardown.

        One of ``"passed"``, ``"failed"``, ``"skipped"`` or ``None`` when the
        owning frontend does not know (yet). Components use it for
        outcome-dependent evidence, for example keeping bulky traces only for
        failures. It is always ``None`` while the component is being opened.
        """
        ...

    def handover(self, resource_key: str) -> AbstractContextManager[None]:
        """Release all live holders of ``resource_key`` for the duration of the block.

        Usable once the component is open (for example from a later call into it).
        Holders must implement :class:`ResourceYield`.
        """
        ...


class ComponentFactory(Protocol):
    """Construct one live component from dependencies and immutable configuration.

    The context manager owns all acquired resources, including partial startup
    cleanup. The host closes periodic workers first, then factories in reverse
    dependency order. Factories must stop/join their own workers before returning
    from cleanup, may publish evidence until then, and must surface cleanup errors.
    """

    def open(
        self,
        context: ComponentContext,
        dependencies: Mapping[str, object],
        config: Mapping[str, Any],
    ) -> AbstractContextManager[object]:
        """Acquire one live component and guarantee deterministic release on context exit."""
        ...
