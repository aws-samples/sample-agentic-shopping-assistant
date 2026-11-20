"""AgentCore Gateway REQUEST interceptor.

Runs before the Gateway calls a target. The agent never forwards the shopper's sign-in
token: it calls the Gateway with a token minted for itself and names the shopper in
`x-aria-actor-id`. This interceptor:

* passes only single JSON-RPC requests for the MCP methods the agent uses (`initialize`,
  `ping`, `tools/list`, `tools/call` and `notifications/*`), and denies anything else,
  batches included;
* requires a well-formed `x-aria-actor-id` on every tool call and passes it on, so
  targets can scope their data to the shopper;
* requires each tool call's token to come from the agent. GATEWAY_AUTH_FLOW names the
  token the agent mints and AGENT_CLIENT_ID the agent's OAuth client:
  - `on_behalf_of`: the token's `act.sub` must be the agent's client, and the header must
    be the actor ID of the token's own subject. The Gateway has validated the token, so
    the shopper it names is authoritative.
  - `m2m`: the token's `client_id` must be the agent's client. A client-credentials token
    names no shopper, so the agent's header is the only user context, trusted because the
    token proves the agent sent it.
  Every request is denied while either setting is missing;
* drops Authorization and every other header, except the non-credential headers named in
  FORWARD_HEADERS, and adds the calling trace's ID as `x-correlation-id`, so a target's logs
  lead back to the agent's trace.

A denied JSON-RPC request is answered here, without calling the target: the Gateway returns
`transformedGatewayResponse` as is, so the agent gets a JSON-RPC error for that call (method
not found for a method the agent doesn't use, ACCESS_DENIED for a failed check) and can tell
a refusal from an outage. A denied message that has no id to answer is raised instead, and so
is any unexpected failure, which the Gateway turns into a refusal of its own.

Headers returned here surface in a Lambda target's
`context.client_context.custom.bedrockAgentCorePropagatedHeaders`; other target types
need them listed in their `allowedRequestHeaders`.

Requires `passRequestHeaders: true` on the interceptor config, otherwise the Gateway
strips request headers before this Lambda sees them.
"""

import base64
import hashlib
import json
import logging
import os
import re

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

OUTPUT_VERSION = "1.0"
ACTOR_HEADER = "x-aria-actor-id"
CORRELATION_HEADER = "x-correlation-id"
# JSON-RPC error codes of a refusal. ACCESS_DENIED is in JSON-RPC's implementation-defined
# server-error range; mcp itself uses -32000 (connection closed) and -32042 from that range.
METHOD_NOT_FOUND = -32601
ACCESS_DENIED = -32003
DENIED_MESSAGE = "Access denied by the Gateway's access check; retrying won't help"
# The log line a denial writes. The Gateway stack's metric filter counts it for the
# interceptor-denial alarm, so a test pins it.
DENIAL_LOG = "interceptor: target invocation denied"
_ACTOR_ID = re.compile(r"[0-9a-f]{64}")
# The agent's OpenTelemetry instrumentation sends a W3C traceparent; AWS adds X-Amzn-Trace-Id to
# every request.
_TRACEPARENT = re.compile(r"[0-9a-f]{2}-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}")
_XRAY_ROOT = re.compile(r"(?:^|;)\s*Root=1-([0-9a-f]{8})-([0-9a-f]{24})\s*(?:;|$)")
_CREDENTIAL_HEADERS = ("authorization", "auth")
_METHODS = frozenset({"initialize", "ping", "tools/list", "tools/call"})
# GATEWAY_AUTH_FLOW values, also accepting the agent's own spelling of each flow.
_FLOWS = {"on_behalf_of": "on_behalf_of", "on_behalf_of_token_exchange": "on_behalf_of", "m2m": "m2m"}  # nosec B105 # auth-flow names, not credentials


class MethodNotAllowed(PermissionError):
    """A JSON-RPC method the agent doesn't use."""


def _header(headers: dict, name: str) -> str:
    """The header's value. Differently cased copies with different values are refused."""
    values = {v for k, v in headers.items() if k.lower() == name and v}
    if len(values) > 1:
        raise PermissionError(f"Conflicting {name} headers")
    return values.pop() if values else ""


def correlation_id(headers: dict) -> str:
    """The calling trace's ID as 32 hex characters, or "" if the request names no trace.

    From traceparent when the agent sent one, so the ID is the agent's own trace; otherwise
    the Root of X-Amzn-Trace-Id. The agent's spans and logs carry the ID in this form.
    """
    lowered = {str(k).lower(): v for k, v in headers.items() if isinstance(v, str)}
    traceparent = _TRACEPARENT.fullmatch(lowered.get("traceparent", "").strip())
    if traceparent and set(traceparent.group(1)) != {"0"}:
        return traceparent.group(1)
    root = _XRAY_ROOT.search(lowered.get("x-amzn-trace-id", ""))
    return root.group(1) + root.group(2) if root else ""


def _claims(authorization: str) -> dict:
    """The bearer token's claims. Not a verification: the Gateway has already validated it."""
    try:
        payload = authorization.removeprefix("Bearer ").split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return claims if isinstance(claims, dict) else {}
    except (IndexError, ValueError):
        return {}


def actor_id(claims: dict, tenant_claim: str = "") -> str:
    """Same derivation as aria_contracts.identity.actor_id; this Lambda stays stdlib-only."""
    issuer, subject = claims.get("iss"), claims.get("sub")
    tenant = claims.get(tenant_claim) if tenant_claim else ""
    if not isinstance(issuer, str) or not issuer.startswith("https://") or not isinstance(subject, str) or not subject:
        raise PermissionError("The token does not name a trusted subject")
    if not isinstance(tenant, str) or (tenant_claim and not tenant):
        raise PermissionError("The token does not name a trusted tenant")
    canonical = json.dumps([issuer, subject, tenant], ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def settings() -> tuple[str, str]:
    """(flow, the agent's client ID) from the environment, or PermissionError if unset."""
    flow = _FLOWS.get(os.environ.get("GATEWAY_AUTH_FLOW", "").strip().lower())
    agent_client = os.environ.get("AGENT_CLIENT_ID", "").strip()
    if not flow or not agent_client:
        raise PermissionError("GATEWAY_AUTH_FLOW (on_behalf_of or m2m) and AGENT_CLIENT_ID must both be set")
    return flow, agent_client


def allowed_method(body) -> str:
    """The JSON-RPC method of an allowed request, or PermissionError."""
    if not isinstance(body, dict):
        raise PermissionError("The request must be a single JSON-RPC message")
    method = body.get("method")
    if not isinstance(method, str) or not (method in _METHODS or method.startswith("notifications/")):
        raise MethodNotAllowed(f"MCP method {str(method)[:64]!r} is not allowed")
    return method


def checked_actor(headers: dict, tenant_claim: str, flow: str, agent_client: str) -> str:
    """The shopper's actor ID for a tool call from the agent, or PermissionError."""
    value = _header(headers, ACTOR_HEADER)
    if not _ACTOR_ID.fullmatch(value):
        raise PermissionError(f"Tool calls need a valid {ACTOR_HEADER}")
    claims = _claims(_header(headers, "authorization"))
    if flow == "on_behalf_of":
        act = claims.get("act")
        if not isinstance(act, dict) or act.get("sub") != agent_client:
            raise PermissionError("The token is not the agent's on-behalf-of token")
        if actor_id(claims, tenant_claim) != value:
            raise PermissionError(f"{ACTOR_HEADER} does not match the token's subject")
    elif claims.get("client_id") != agent_client:
        raise PermissionError("The token was not issued to the agent's client")
    return value


def _forward_headers(original: dict, correlation: str, actor: str, forward_names: frozenset) -> dict:
    headers = {}
    if correlation:
        headers[CORRELATION_HEADER] = correlation
    if actor:
        headers[ACTOR_HEADER] = actor
    # Custom, non-credential headers named in FORWARD_HEADERS. Matched case
    # insensitively; the caller's original casing is preserved. Credentials are
    # never carried this way, and neither header set above can be replaced.
    lowered = {k.lower(): (k, v) for k, v in original.items()}
    for name in forward_names:
        match = lowered.get(name.lower())
        if match and match[0].lower() not in (*_CREDENTIAL_HEADERS, ACTOR_HEADER, CORRELATION_HEADER):
            headers[match[0]] = match[1]
    return headers


def _refusal(body, denied: PermissionError) -> dict | None:
    """The Gateway's answer to a denied request: a JSON-RPC error for its id.

    None for a message with no id to answer (a notification, a batch, a body that isn't a
    JSON-RPC message), which the caller raises instead.
    """
    request_id = body.get("id") if isinstance(body, dict) else None
    if not isinstance(request_id, (int, str)) or isinstance(request_id, bool) or "method" not in body:
        return None
    if isinstance(denied, MethodNotAllowed):
        error = {"code": METHOD_NOT_FOUND, "message": "Method not found"}
    else:
        error = {"code": ACCESS_DENIED, "message": DENIED_MESSAGE}
    return {
        "interceptorOutputVersion": OUTPUT_VERSION,
        "mcp": {"transformedGatewayResponse": {"statusCode": 200, "body": {"jsonrpc": "2.0", "id": request_id, "error": error}}},
    }


def lambda_handler(event, _context):
    mcp = event.get("mcp") or {}
    gateway_request = mcp.get("gatewayRequest") or {}
    body = gateway_request.get("body")
    original = gateway_request.get("headers") or {}

    forward_names = frozenset(filter(None, os.environ.get("FORWARD_HEADERS", "").split(",")))
    tenant_claim = os.environ.get("OIDC_TENANT_CLAIM", "")
    try:
        flow, agent_client = settings()
        method = allowed_method(body)
        # Listing tools needs no shopper; calling one always does.
        actor = checked_actor(original, tenant_claim, flow, agent_client) if method == "tools/call" else ""
        headers = _forward_headers(original, correlation_id(original), actor, forward_names)
        # Header names only, never values: what the Gateway passed in, and what goes on.
        logger.info("interceptor: method=%s received=%s forwarded=%s", method,
                    sorted({str(name).lower() for name in original}), sorted(headers))
    except PermissionError as denied:
        logger.warning("%s: %s", DENIAL_LOG, denied)
        refusal = _refusal(body, denied)
        if refusal is None:
            raise
        return refusal
    except Exception:
        logger.exception("interceptor failed; target invocation denied")
        raise

    return {
        "interceptorOutputVersion": OUTPUT_VERSION,
        "mcp": {"transformedGatewayRequest": {"headers": headers, "body": body}},
    }
