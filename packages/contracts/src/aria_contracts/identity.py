"""Stateless ownership, independent of AgentCore's session lifecycle.

IDs contain SHA-256(canonical verified principal) and a 128-bit random nonce.
The digest is a namespace, not a secret or an authentication credential. Every
entrypoint must compare it against identity from a VERIFIED token. 97 characters
fit both Runtime and Memory. Any session ID not in this principal-bound form fails closed.
"""
import base64
import hashlib
import hmac
import json
import re
import uuid


def actor_id(claims: dict, tenant_claim: str = "") -> str:
    issuer, subject = claims.get("iss"), claims.get("sub")
    tenant = claims.get(tenant_claim) if tenant_claim else ""
    if not isinstance(issuer, str) or not issuer.startswith("https://") or not isinstance(subject, str) or not subject:
        raise ValueError("Missing trusted issuer/subject")
    if not isinstance(tenant, str) or (tenant_claim and not tenant):
        raise ValueError("Missing trusted tenant")
    canonical = json.dumps([issuer, subject, tenant], ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def new_session_id(owner: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{64}", owner):
        raise ValueError("Invalid principal namespace")
    return f"{owner}_{uuid.uuid4().hex}"


def require_session_owner(session_id: str, owner: str) -> None:
    if not re.fullmatch(r"[a-f0-9]{64}_[a-f0-9]{32}", session_id) or not hmac.compare_digest(session_id[:64], owner):
        raise ValueError("Session does not belong to this caller")


def runtime_actor(token: str, issuer: str, tenant_claim: str = "") -> str:
    """Decode ONLY after AgentCore CUSTOM_JWT authentication, never at an API edge."""
    try:
        raw = token.removeprefix("Bearer ").split(".")
        if len(raw) != 3:
            raise ValueError("Malformed token")
        claims = json.loads(base64.urlsafe_b64decode(raw[1] + "=" * (-len(raw[1]) % 4)))
        if not isinstance(claims, dict):
            raise ValueError("Malformed token")
        if not issuer or claims.get("iss") != issuer:
            raise ValueError("Unexpected runtime issuer")
        return actor_id(claims, tenant_claim)
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raise ValueError("Missing trusted caller") from exc
