from dataclasses import FrozenInstanceError, replace

import pytest

from autospan.api import ClaimMode, ComponentSpec, PlanError, ResourceClaim
from autospan._core.resource_claims import validate_resource_claims


@pytest.mark.parametrize("mode", list(ClaimMode))
def test_resource_claim_accepts_enum_modes(mode):
    assert ResourceClaim("serial:COM1", mode).mode is mode


@pytest.mark.parametrize("mode", list(ClaimMode))
def test_resource_claim_normalizes_string_modes(mode):
    assert ResourceClaim("serial:COM1", mode.value).mode is mode


def test_resource_claim_defaults_to_exclusive():
    assert ResourceClaim("serial:COM1").mode is ClaimMode.EXCLUSIVE


@pytest.mark.parametrize("mode", ["", " ", "unknown", "SHARED", "Exclusive", " shared"])
def test_resource_claim_rejects_unknown_modes(mode):
    with pytest.raises(ValueError):
        ResourceClaim("serial:COM1", mode)


@pytest.mark.parametrize("mode", [None, False, 0, b"shared", [], {}, object()])
def test_resource_claim_rejects_invalid_mode_types(mode):
    with pytest.raises(TypeError, match="ResourceClaim.mode"):
        ResourceClaim("serial:COM1", mode)


@pytest.mark.parametrize("key", ["", " ", "\t\n", "\u2003"])
def test_resource_claim_rejects_empty_keys(key):
    with pytest.raises(ValueError, match="ResourceClaim.key must not be empty"):
        ResourceClaim(key)


@pytest.mark.parametrize("key", [None, False, 0, b"serial:COM1", [], {}, object()])
def test_resource_claim_rejects_invalid_key_types(key):
    with pytest.raises(TypeError, match="ResourceClaim.key must be a string"):
        ResourceClaim(key)


def test_resource_claim_preserves_valid_construction_behavior():
    key = " serial:COM1 "
    claim = ResourceClaim(key=key, mode=ClaimMode.SHARED)
    assert claim.key == key
    assert claim == ResourceClaim(key, "shared")
    assert hash(claim) == hash(ResourceClaim(key, ClaimMode.SHARED))
    assert replace(claim, mode="exclusive") == ResourceClaim(key)
    with pytest.raises(FrozenInstanceError):
        claim.mode = ClaimMode.EXCLUSIVE


def test_normalized_exclusive_string_claim_conflicts():
    with pytest.raises(PlanError, match="Resource claim conflict"):
        validate_resource_claims(
            (
                ComponentSpec("a", "x", claims=(ResourceClaim("serial:COM1", "exclusive"),)),
                ComponentSpec("b", "x", claims=(ResourceClaim("serial:COM1", "shared"),)),
            )
        )


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
