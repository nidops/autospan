"""Dependency graph validation for runtime components."""

from __future__ import annotations

from collections import deque

from autospan.api.component import ComponentSpec
from autospan.api.errors import PlanError


def topological_order(components: tuple[ComponentSpec, ...]) -> tuple[ComponentSpec, ...]:
    """Validate names/dependencies/cycles and return dependency-first order."""

    by_name: dict[str, ComponentSpec] = {}
    for component in components:
        if component.name in by_name:
            raise PlanError(f"Duplicate component name: {component.name!r}")
        by_name[component.name] = component

    indegree = {name: 0 for name in by_name}
    dependents: dict[str, list[str]] = {name: [] for name in by_name}
    for component in components:
        for dependency in component.dependencies:
            if dependency == component.name:
                raise PlanError(f"Component {component.name!r} cannot depend on itself")
            if dependency not in by_name:
                raise PlanError(
                    f"Component {component.name!r} depends on missing component {dependency!r}"
                )
            indegree[component.name] += 1
            dependents[dependency].append(component.name)

    ready = deque(sorted(name for name, count in indegree.items() if count == 0))
    ordered: list[ComponentSpec] = []
    while ready:
        name = ready.popleft()
        ordered.append(by_name[name])
        for dependent in sorted(dependents[name]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)

    if len(ordered) != len(components):
        cyclic = sorted(name for name, count in indegree.items() if count > 0)
        raise PlanError(f"Runtime dependency cycle detected involving: {', '.join(cyclic)}")
    return tuple(ordered)
