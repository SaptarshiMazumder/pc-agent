"""The `orgs` access-token claim (tenancy E1) — how the daemon learns membership with zero hops.

Two decoders exist (the issuer's own verify, and the JWKS verifier both images ship) and one
encoder. These tests pin that all three agree, that the personal-only token is byte-identical
to what it always was (no claim at all, not an empty one), and that a malformed claim degrades
to NO orgs — the fail-closed direction, because an org id that decodes out of garbage would be
an identity somebody never proved.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import jwt as pyjwt
import pytest

from identity.domain.principal import Principal
from identity.domain.token import orgs_from_wire, orgs_to_wire
from identity.infrastructure.jwt_token_issuer import JwtTokenIssuer

ISSUER = "https://accounts.test.invalid"
ORGS = (("org_kajima", "member"), ("org_side", "owner"))


class _Keys:
    """A minimal KeyStore: one Ed25519 key, generated once per test run."""

    def __init__(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        private = Ed25519PrivateKey.generate()
        pem = private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        pub = private.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode()
        from identity.application.interfaces.key_store import SigningKey

        self._key = SigningKey(
            kid="k1", alg="EdDSA", public_pem=pub, private_pem=pem,
            created_at=time.time(), expires_at=0,
        )

    def active(self):
        return self._key

    def verification_keys(self):
        return [self._key]


@pytest.fixture
def issuer():
    return JwtTokenIssuer(_Keys(), issuer=ISSUER)


def _principal(orgs=()):
    return Principal(account_id="acct_a", email="a@example.com", orgs=tuple(orgs))


def test_the_claim_round_trips_through_issue_and_verify(issuer):
    token, minted = issuer.issue(_principal(ORGS))
    assert minted.orgs == ORGS
    assert issuer.verify(token).orgs == ORGS


def test_a_personal_only_token_carries_no_claim_at_all(issuer):
    """Not an empty list — NO key. The common token's size does not change by a byte, and an
    old verifier sees exactly the payload it always saw."""
    token, _ = issuer.issue(_principal(()))
    payload = pyjwt.decode(token, options={"verify_signature": False}, audience=None)
    assert "orgs" not in payload
    assert issuer.verify(token).orgs == ()


def test_the_jwks_verifier_decodes_the_same_claim(issuer, tmp_path):
    """The daemon-side decoder (the one that makes connect zero-hop) must agree with the
    issuer's own — two decoders that drift is how one image grants what the other denies."""
    from identity.infrastructure.jwks_verifier import JwksVerifier

    token, _ = issuer.issue(_principal(ORGS))
    verifier = JwksVerifier(jwks_uri="https://unused.invalid/jwks", issuer=ISSUER)
    verifier._absorb(issuer.public_jwks())
    claims = verifier._decode(token, "k1")
    assert claims.orgs == ORGS


def test_wire_decoding_fails_closed_on_every_malformed_shape():
    assert orgs_from_wire(None) == ()
    assert orgs_from_wire("garbage") == ()
    assert orgs_from_wire([{"role": "admin"}]) == ()  # id missing: dropped, never guessed
    assert orgs_from_wire([{"id": ""}, 42, None]) == ()
    # a bare id gets the least-privileged role, never a guessed elevated one
    assert orgs_from_wire([{"id": "org_x"}]) == (("org_x", "member"),)
    # tuple/list entries (the internal shape) decode too — one codec for both directions
    assert orgs_from_wire([("org_x", "admin")]) == (("org_x", "admin"),)


def test_wire_encoding_is_the_exact_inverse():
    assert orgs_from_wire(orgs_to_wire(ORGS)) == ORGS


def test_with_scopes_preserves_membership():
    """The downscoping seam (agent-app tokens) must not silently strip orgs — a narrowed
    token is narrower in SCOPES, not secretly a different person."""
    narrowed = _principal(ORGS).with_scopes(("chat",))
    assert narrowed.orgs == ORGS
