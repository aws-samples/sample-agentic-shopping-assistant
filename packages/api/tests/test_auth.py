import time
from types import SimpleNamespace

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

import auth
from aria_contracts import events
from aria_contracts.identity import actor_id
from config import get_config

# An identity provider outage, coded so the web offers a retry rather than a new sign-in.
OUTAGE = {"code": "upstream", "message": "Identity provider temporarily unavailable"}
assert OUTAGE["code"] in events.ERROR_CODES


@pytest.fixture
def key(monkeypatch):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setenv("OIDC_ISSUER", "https://issuer.example")
    monkeypatch.setenv("OIDC_ALLOWED_CLIENTS", "web")
    get_config.cache_clear()
    monkeypatch.setattr(auth, "signing_keys", lambda _: SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=private.public_key())))
    yield private
    get_config.cache_clear()


def token(key, **claims):
    payload = dict(iss="https://issuer.example", sub="alice", exp=int(time.time()) + 120, client_id="web", token_use="access", scope="aria/chat")  # nosec B106 # token_use is a JWT claim value in a test fixture, not a credential
    payload.update(claims)
    return "Bearer " + jwt.encode(payload, key, algorithm="RS256")


def test_verified_access_token(key):
    assert auth.require_caller(token(key))["actor_id"] == actor_id({"iss": "https://issuer.example", "sub": "alice"})


@pytest.mark.parametrize("change", [{"iss": "https://evil.example"}, {"exp": 1}, {"client_id": "other"}, {"token_use": "id"}, {"scope": "openid"}, {"sub": ""}])  # nosec B105 # a token_use claim value, not a credential
def test_rejects_invalid_claims(key, change):
    with pytest.raises(HTTPException) as exc:
        auth.require_caller(token(key, **change))
    assert exc.value.status_code in (401, 403)


def test_rejects_forged_signature(key):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(HTTPException, match="Invalid access token"):
        auth.require_caller(token(other))


def test_byo_oidc_preserves_exact_issuer_including_trailing_slash(key, monkeypatch):
    monkeypatch.setenv("AUTH_PROVIDER", "oidc")
    monkeypatch.setenv("OIDC_ISSUER", "https://issuer.example/")
    monkeypatch.setenv("OIDC_AUDIENCES", "aria-api")
    monkeypatch.delenv("OIDC_ALLOWED_CLIENTS", raising=False)
    get_config.cache_clear()
    caller = auth.require_caller(token(key, iss="https://issuer.example/", aud="aria-api"))
    assert caller["issuer"] == "https://issuer.example/"


def test_jwks_rotates_on_unknown_key_id(key, monkeypatch):
    import json
    old = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    old["kid"] = "old"
    new_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    new = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(new_key.public_key()))
    new["kid"] = "new"
    client = jwt.PyJWKClient("https://issuer.example/keys")
    client.jwk_set_cache.put({"keys": [old]})
    refreshed = []
    def fetch():
        refreshed.append(True)
        client.jwk_set_cache.put({"keys": [new]})
        return {"keys": [new]}
    monkeypatch.setattr(client, "fetch_data", fetch)
    monkeypatch.setattr(auth, "signing_keys", lambda _: client)
    payload = dict(iss="https://issuer.example", sub="alice", exp=int(time.time())+120, client_id="web", token_use="access", scope="aria/chat")  # nosec B106 # token_use is a JWT claim value in a test fixture, not a credential
    bearer = "Bearer " + jwt.encode(payload, new_key, algorithm="RS256", headers={"kid": "new"})
    assert auth.require_caller(bearer)["actor_id"]
    assert refreshed == [True]


def test_an_unreachable_jwks_endpoint_is_503_not_401(key, monkeypatch):
    import urllib.error
    import urllib.request

    # The real PyJWKClient turns the network failure into PyJWKClientConnectionError, a PyJWTError.
    def unreachable(*args, **kwargs):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", unreachable)
    monkeypatch.setattr(auth, "signing_keys", lambda _: jwt.PyJWKClient("https://issuer.example/keys"))
    bearer = "Bearer " + jwt.encode({"sub": "alice"}, key, algorithm="RS256", headers={"kid": "k1"})
    with pytest.raises(HTTPException) as exc:
        auth.require_caller(bearer)
    assert (exc.value.status_code, exc.value.detail) == (503, OUTAGE)


def test_a_key_lookup_that_fails_on_the_network_is_503(key, monkeypatch):
    def lookup(_):
        raise OSError("connection reset")

    monkeypatch.setattr(auth, "signing_keys", lambda _: SimpleNamespace(get_signing_key_from_jwt=lookup))
    with pytest.raises(HTTPException) as exc:
        auth.require_caller(token(key))
    assert (exc.value.status_code, exc.value.detail) == (503, OUTAGE)


def test_an_unknown_key_id_is_still_401(key, monkeypatch):
    import json

    published = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())) | {"kid": "published"}
    client = jwt.PyJWKClient("https://issuer.example/keys")
    monkeypatch.setattr(client, "fetch_data", lambda: {"keys": [published]})
    monkeypatch.setattr(auth, "signing_keys", lambda _: client)
    bearer = "Bearer " + jwt.encode({"sub": "alice"}, key, algorithm="RS256", headers={"kid": "unknown"})
    with pytest.raises(HTTPException) as exc:
        auth.require_caller(bearer)
    # A 401 body is plain text, not coded: the web offers a new sign-in for any 401.
    assert (exc.value.status_code, exc.value.detail) == (401, "Invalid access token")


@pytest.mark.parametrize(
    "discovery",
    [
        httpx.ConnectError("unreachable"),
        httpx.Response(500, request=httpx.Request("GET", "https://issuer.example")),
        httpx.Response(200, text="<html>maintenance</html>", request=httpx.Request("GET", "https://issuer.example")),
        httpx.Response(200, json=["not", "a", "document"], request=httpx.Request("GET", "https://issuer.example")),
        httpx.Response(200, json={"issuer": "https://evil.example", "jwks_uri": "https://evil.example/keys"}, request=httpx.Request("GET", "https://issuer.example")),
    ],
)
def test_an_identity_provider_without_valid_discovery_is_503(monkeypatch, discovery):
    monkeypatch.setenv("OIDC_ISSUER", "https://issuer.example")
    monkeypatch.setenv("OIDC_ALLOWED_CLIENTS", "web")
    get_config.cache_clear()
    auth.signing_keys.cache_clear()

    def get(*args, **kwargs):
        if isinstance(discovery, Exception):
            raise discovery
        return discovery

    monkeypatch.setattr(auth.httpx, "get", get)
    try:
        with pytest.raises(HTTPException) as exc:
            auth.require_caller("Bearer a.b.c")
        assert (exc.value.status_code, exc.value.detail) == (503, OUTAGE)
    finally:
        auth.signing_keys.cache_clear()
        get_config.cache_clear()
