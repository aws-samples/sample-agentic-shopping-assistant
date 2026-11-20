"""Validate access tokens before every protected API operation."""
from functools import lru_cache

import httpx
import jwt
from fastapi import Header, HTTPException

from config import get_config
from aria_contracts.identity import actor_id


@lru_cache(maxsize=8)
def signing_keys(issuer: str) -> jwt.PyJWKClient:
    discovery_url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    response = httpx.get(discovery_url, timeout=5, follow_redirects=False)
    response.raise_for_status()
    discovery = response.json()
    uri = discovery.get("jwks_uri", "") if isinstance(discovery, dict) else ""
    if not isinstance(discovery, dict) or discovery.get("issuer") != issuer or not isinstance(uri, str) or not uri.startswith("https://"):
        raise ValueError("Invalid OIDC discovery document")
    return jwt.PyJWKClient(uri, timeout=5, lifespan=300)


UNAVAILABLE = "Identity provider temporarily unavailable"


def unavailable() -> HTTPException:
    """An identity provider outage: a 503 coded `upstream`, so the web offers a retry, not a new sign-in."""
    return HTTPException(503, detail={"code": "upstream", "message": UNAVAILABLE})


def require_caller(authorization: str = Header(default="")) -> dict[str, str]:
    # A sync dependency runs in FastAPI's thread pool; JWKS refresh cannot block
    # the event loop. Neither unverified claims nor body/header user IDs authorize.
    config = get_config()
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token or len(token) > 16384:
        raise HTTPException(401, "Missing or malformed bearer token")
    if not config.oidc_issuer or not (config.oidc_audiences or config.oidc_allowed_clients):
        raise HTTPException(503, "Identity provider is not configured")
    try:
        # Discovery concerns the identity provider only, never the caller's token: any failure is an outage.
        jwks = signing_keys(config.oidc_issuer)
    except (httpx.HTTPError, OSError, ValueError):
        raise unavailable() from None
    try:
        key = jwks.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token, key.key, algorithms=["RS256", "ES256"], issuer=config.oidc_issuer,
            audience=config.oidc_audiences or None,
            options={"require": ["exp", "iss", "sub"], "verify_aud": bool(config.oidc_audiences)},
            leeway=10,
        )
        if not isinstance(claims["sub"], str) or not claims["sub"]:
            raise ValueError("Missing subject")
        if config.oidc_allowed_clients and claims.get("client_id") not in config.oidc_allowed_clients:
            raise ValueError("Wrong client")
        if config.auth_provider == "cognito" and claims.get("token_use") != "access":
            raise ValueError("Expected access token")
        scopes = claims.get("scope", "")
        if not isinstance(scopes, str) or not set(config.oidc_scopes).issubset(scopes.split()):
            raise HTTPException(403, "Insufficient scope")
        tenant = claims.get(config.oidc_tenant_claim, "") if config.oidc_tenant_claim else ""
        if not isinstance(tenant, str) or (config.oidc_tenant_claim and not tenant):
            raise ValueError("Missing trusted tenant")
        return {"token": f"Bearer {token}", "actor_id": actor_id(claims, config.oidc_tenant_claim), "issuer": claims["iss"], "tenant": tenant}
    except HTTPException:
        raise
    except jwt.PyJWKClientConnectionError:
        # The JWKS endpoint is unreachable. PyJWT raises this as a PyJWTError, so it has to be
        # caught before the 401 below: an outage must not look like a bad token to the client.
        raise unavailable() from None
    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(401, "Invalid access token") from None
    except (httpx.HTTPError, OSError):
        raise unavailable() from None
