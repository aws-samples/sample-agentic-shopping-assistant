"""The token the agent mints to call Gateway tools.

The shopper's sign-in JWT never leaves the agent. The Runtime validated it, and the agent reads
its claims to know who the shopper is, but it never forwards the JWT. For the Gateway, the
agent gets a token of its own from AgentCore Identity:

* ON_BEHALF_OF_TOKEN_EXCHANGE (the default with managed Cognito): an RFC 8693 delegation
  token for the Gateway's resource server. Its `sub` is the shopper and `act.sub` is the
  agent's client, and its scopes can only narrow the shopper's own.
* M2M: a client-credentials token for the agent's client. It says nothing about the shopper,
  so the `x-aria-actor-id` header is the only user context a target gets.

Both flows need the workload access token the Runtime hands each request in the
`WorkloadAccessToken` header, which AgentCore Identity binds to the verified shopper.
Minted tokens are cached until shortly before they expire, because the Gateway is called
on many turns and each mint is a network round trip.
"""

import base64
import json
import threading
import time
from typing import Any, Optional

import boto3
from botocore.config import Config

FLOWS = ("ON_BEHALF_OF_TOKEN_EXCHANGE", "M2M")
# A mint runs inside a Gateway request, so a slow AgentCore Identity call must fail fast.
IDENTITY_CLIENT_CONFIG = Config(
    connect_timeout=3, read_timeout=10, retries={"mode": "standard", "total_max_attempts": 3}
)
_REFRESH_MARGIN_SECONDS = 60
# AgentCore Identity labels the subject token `urn:ietf:params:oauth:token-type:jwt` unless the
# request says otherwise, and Cognito rejects that type ("Unsupported token type"). The subject
# is the shopper's OAuth access token, so the agent labels it as one.
SUBJECT_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"  # nosec B105 # an RFC 8693 token-type URN, not a credential
# Used when a minted token isn't a JWT, so its expiry can't be read.
_OPAQUE_TOKEN_TTL_SECONDS = 300


def token_expiry(token: str, now: float) -> float:
    """The token's `exp`, or a short default for tokens whose expiry can't be read."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return float(claims["exp"])
    except (IndexError, KeyError, TypeError, ValueError):
        return now + _OPAQUE_TOKEN_TTL_SECONDS


class DownstreamToken:
    """Mints and caches the agent's Gateway token. Thread-safe: MCP calls run on their own thread."""

    def __init__(
        self,
        *,
        provider: str,
        flow: str,
        scopes: tuple[str, ...],
        region: str,
        audience: str = "",
        client: Any = None,
        clock=time.time,
    ):
        if flow not in FLOWS:
            raise ValueError(f"Unsupported Gateway auth flow {flow!r}; expected one of {FLOWS}")
        if not provider or not scopes:
            raise ValueError("A Gateway credential provider and at least one scope are required")
        self.provider, self.flow, self.scopes, self.audience = provider, flow, tuple(scopes), audience
        self._region = region
        self._client = client
        self._clock = clock
        self._lock = threading.Lock()
        self._workload_token: Optional[str] = None
        self._token: Optional[str] = None
        self._expires_at = 0.0

    def set_workload_token(self, workload_token: Optional[str]) -> None:
        """Record this request's workload access token. The Runtime sends one per request."""
        if workload_token:
            with self._lock:
                self._workload_token = workload_token

    def bearer(self) -> str:
        """`Bearer <token>` for the Gateway, minting a new token when the cached one is near expiry."""
        with self._lock:
            now = self._clock()
            if self._token and now < self._expires_at - _REFRESH_MARGIN_SECONDS:
                return f"Bearer {self._token}"
            if not self._workload_token:
                raise PermissionError("No workload access token for this request; cannot call Gateway tools")
            request = {
                "workloadIdentityToken": self._workload_token,
                "resourceCredentialProviderName": self.provider,
                "scopes": list(self.scopes),
                "oauth2Flow": self.flow,
            }
            if self.audience:
                request["audiences"] = [self.audience]
            if self.flow == "ON_BEHALF_OF_TOKEN_EXCHANGE":
                request["customParameters"] = {"subject_token_type": SUBJECT_TOKEN_TYPE}
            response = self._identity().get_resource_oauth2_token(**request)
            token = response.get("accessToken")
            if not token:
                # An authorization URL would mean a user-consent flow, which the Gateway token never uses.
                raise PermissionError("AgentCore Identity returned no access token for the Gateway")
            self._token, self._expires_at = token, token_expiry(token, now)
            return f"Bearer {token}"

    def _identity(self):
        if self._client is None:
            self._client = boto3.client("bedrock-agentcore", region_name=self._region, config=IDENTITY_CLIENT_CONFIG)
        return self._client
