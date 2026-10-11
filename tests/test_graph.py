import pytest

from autospan.api import ComponentSpec, PlanError
from autospan._core.graph import topological_order


def test_topological_order_is_dependency_first():
    ordered = topological_order(
        (
            ComponentSpec("application", "x", dependencies=("service",)),
            ComponentSpec("transport", "x"),
            ComponentSpec("service", "x", dependencies=("transport",)),
        )
    )
    assert [item.name for item in ordered] == ["transport", "service", "application"]


def test_missing_dependency_fails_before_runtime():
    with pytest.raises(PlanError, match="missing component"):
        topological_order((ComponentSpec("application", "x", dependencies=("service",)),))


def test_cycle_fails_before_runtime():
    with pytest.raises(PlanError, match="cycle"):
        topological_order(
            (
                ComponentSpec("a", "x", dependencies=("b",)),
                ComponentSpec("b", "x", dependencies=("a",)),
            )
        )
