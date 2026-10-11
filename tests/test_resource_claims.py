import pytest

from autospan.api import ClaimMode, ComponentSpec, PlanError, ResourceClaim
from autospan._core.resource_claims import validate_resource_claims


def test_two_shared_claims_are_allowed():
    validate_resource_claims(
        (
            ComponentSpec("a", "x", claims=(ResourceClaim("tcp:x", ClaimMode.SHARED),)),
            ComponentSpec("b", "x", claims=(ResourceClaim("tcp:x", ClaimMode.SHARED),)),
        )
    )


def test_exclusive_claim_conflicts():
    with pytest.raises(PlanError, match="Resource claim conflict"):
        validate_resource_claims(
            (
                ComponentSpec("a", "x", claims=(ResourceClaim("serial:COM1"),)),
                ComponentSpec("b", "x", claims=(ResourceClaim("serial:COM1"),)),
            )
        )
