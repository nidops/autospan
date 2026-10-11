"""Generic physical/logical resource collision detection."""

from __future__ import annotations

from autospan.api.component import ClaimMode, ComponentSpec
from autospan.api.errors import PlanError


def validate_resource_claims(components: tuple[ComponentSpec, ...]) -> None:
    seen: dict[str, list[tuple[str, ClaimMode]]] = {}
    for component in components:
        for claim in component.claims:
            previous = seen.setdefault(claim.key, [])
            conflicts = [
                owner
                for owner, mode in previous
                if claim.mode is ClaimMode.EXCLUSIVE or mode is ClaimMode.EXCLUSIVE
            ]
            if conflicts:
                owners = ", ".join(conflicts)
                raise PlanError(
                    f"Resource claim conflict for {claim.key!r}: "
                    f"{component.name!r} conflicts with {owners}"
                )
            previous.append((component.name, claim.mode))
