"""The identity contract: actor IDs and principal-bound session IDs.

Changing a derivation changes every shopper's actor ID and Memory namespace, so the golden vectors
below are pinned. The Gateway interceptor's stdlib copy of `actor_id` is pinned to the same values
(packages/mcp-servers/gateway-interceptor/tests/test_handler.py).
"""

import base64
import json
import re

import pytest

from aria_contracts.identity import actor_id, new_session_id, require_session_owner, runtime_actor

COGNITO_ISSUER = "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_EXAMPLE"
ALICE = "974dfe115d04f5a70c5c11a331fc916c3f7401d96d6e7f2d1c03918fca872388"
BOB_AT_ACME = "c75b771dfd777e490c7b511be8bc130ff6d07d9cdab7d3004d6464bd8e657f28"
SESSION_FORMAT = re.compile(r"[a-f0-9]{64}_[a-f0-9]{32}")


def jwt(claims: dict) -> str:
    def part(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return f"{part({'alg': 'RS256'})}.{part(claims)}.signature"


class TestActorId:
    def test_golden_vectors(self):
        assert actor_id({"iss": COGNITO_ISSUER, "sub": "alice-sub"}) == ALICE
        assert actor_id({"iss": "https://issuer.example", "sub": "bob", "tenant": "acme"}, "tenant") == BOB_AT_ACME

    def test_is_the_sha256_of_the_canonical_principal(self):
        import hashlib

        canonical = json.dumps([COGNITO_ISSUER, "alice-sub", ""], ensure_ascii=True, separators=(",", ":"))
        assert hashlib.sha256(canonical.encode()).hexdigest() == ALICE

    def test_other_claims_do_not_change_it(self):
        claims = {"iss": COGNITO_ISSUER, "sub": "alice-sub", "client_id": "web", "scope": "aria/chat", "exp": 1}
        assert actor_id(claims) == ALICE

    def test_a_tenant_claim_counts_only_when_configured(self):
        assert actor_id({"iss": COGNITO_ISSUER, "sub": "alice-sub", "tenant": "acme"}) == ALICE
        assert actor_id({"iss": COGNITO_ISSUER, "sub": "alice-sub", "tenant": "acme"}, "tenant") != ALICE

    def test_issuer_subject_and_tenant_are_kept_apart(self):
        # The canonical form is a JSON array, so moving characters across fields changes the ID.
        a = actor_id({"iss": "https://issuer.example", "sub": "ab", "t": "c"}, "t")
        b = actor_id({"iss": "https://issuer.example", "sub": "a", "t": "bc"}, "t")
        c = actor_id({"iss": "https://issuer.example", "sub": 'a","bc'})
        assert len({a, b, c}) == 3

    def test_issuers_namespace_subjects(self):
        assert actor_id({"iss": "https://one.example", "sub": "alice"}) != actor_id({"iss": "https://two.example", "sub": "alice"})

    @pytest.mark.parametrize(
        "claims",
        [
            {"sub": "alice-sub"},
            {"iss": "http://issuer.example", "sub": "alice-sub"},
            {"iss": COGNITO_ISSUER},
            {"iss": COGNITO_ISSUER, "sub": ""},
            {"iss": COGNITO_ISSUER, "sub": 42},
            {"iss": ["https://issuer.example"], "sub": "alice-sub"},
        ],
    )
    def test_requires_an_https_issuer_and_a_subject(self, claims):
        with pytest.raises(ValueError):
            actor_id(claims)

    @pytest.mark.parametrize("tenant", [None, "", 7])
    def test_a_configured_tenant_claim_is_required(self, tenant):
        claims = {"iss": COGNITO_ISSUER, "sub": "alice-sub"} | ({} if tenant is None else {"tenant": tenant})
        with pytest.raises(ValueError):
            actor_id(claims, "tenant")


class TestSessionIds:
    def test_format_and_owner(self):
        session = new_session_id(ALICE)
        assert SESSION_FORMAT.fullmatch(session)
        # Fits both the Runtime (>= 33 characters) and Memory session ID limits.
        assert len(session) == 97
        assert session.startswith(ALICE + "_")
        require_session_owner(session, ALICE)

    def test_ids_are_random(self):
        assert len({new_session_id(ALICE) for _ in range(50)}) == 50

    @pytest.mark.parametrize("owner", ["", "alice", ALICE.upper(), ALICE[:-1], ALICE + "0", "g" * 64])
    def test_needs_a_principal_namespace(self, owner):
        with pytest.raises(ValueError):
            new_session_id(owner)

    def test_another_owner_is_rejected(self):
        with pytest.raises(ValueError):
            require_session_owner(new_session_id(ALICE), BOB_AT_ACME)

    @pytest.mark.parametrize(
        "session",
        [
            "",
            "s" + "a" * 32,  # long enough for the Runtime, but no principal namespace
            "8c7f0aac-97c4-4a2e-b3ce-fd2e1ac87c1e",
            ALICE,
            ALICE + "_",
            ALICE + "-" + "0" * 32,
            ALICE + "_" + "0" * 31,
            ALICE + "_" + "0" * 33,
            ALICE + "_" + "A" * 32,
            ALICE.upper() + "_" + "0" * 32,
            ALICE + "_" + "0" * 32 + "\n",
        ],
    )
    def test_malformed_and_unbound_ids_fail_closed(self, session):
        with pytest.raises(ValueError):
            require_session_owner(session, ALICE)


class TestRuntimeActor:
    def test_reads_the_runtime_verified_token(self):
        token = jwt({"iss": COGNITO_ISSUER, "sub": "alice-sub"})
        assert runtime_actor(token, COGNITO_ISSUER) == ALICE
        assert runtime_actor("Bearer " + token, COGNITO_ISSUER) == ALICE

    def test_uses_the_tenant_claim(self):
        token = jwt({"iss": "https://issuer.example", "sub": "bob", "tenant": "acme"})
        assert runtime_actor(token, "https://issuer.example", "tenant") == BOB_AT_ACME

    @pytest.mark.parametrize(
        "token,issuer",
        [
            ("", COGNITO_ISSUER),
            ("only.two", COGNITO_ISSUER),
            ("a.!!!.c", COGNITO_ISSUER),
            (jwt({"iss": COGNITO_ISSUER, "sub": "alice-sub"}), ""),
            (jwt({"iss": "https://evil.example", "sub": "alice-sub"}), COGNITO_ISSUER),
            (jwt({"iss": COGNITO_ISSUER}), COGNITO_ISSUER),
            (jwt(["not", "an", "object"]), COGNITO_ISSUER),
        ],
    )
    def test_rejects_anything_but_the_expected_issuer(self, token, issuer):
        with pytest.raises(ValueError, match="Missing trusted caller"):
            runtime_actor(token, issuer)
