# The Gateway interceptor

Status: verified

The Gateway's REQUEST interceptor checks the shopper's actor ID on every tool call and decides which headers reach a target. It runs as a stdlib-only Lambda in `packages/mcp-servers/gateway-interceptor`.

The saved-items sample target behind it, and the contract any Lambda target follows, are in [Gateway onboarding](../guides/gateway-onboarding.md#lambda-target-contract).

## Interceptor flow

The interceptor reads `event.mcp.gatewayRequest` and denies the request unless:

1. `GATEWAY_AUTH_FLOW` (`on_behalf_of` or `m2m`) and `AGENT_CLIENT_ID` are both set. CDK sets them from `gateway.agentAuth`; without them every request is denied, `initialize` included.
2. The body is a single JSON-RPC message for a method the agent uses: `initialize`, `ping`, `tools/list`, `tools/call` or `notifications/*`. Batches and other methods are denied.
3. On `tools/call`, `x-aria-actor-id` is exactly 64 lowercase hex characters, and the token proves the agent sent it:
   - `on_behalf_of`: `act.sub` is the agent's client, and the header equals the actor ID derived from the token's `iss`, `sub` and the configured tenant claim, the same derivation as `aria_contracts.identity.actor_id`.
   - `m2m`: `client_id` is the agent's client. A client-credentials token names no shopper, so the agent's header is the only user context; the token proves the agent set it.

Two copies of the actor header or of `Authorization` that differ only in casing and carry different values are refused. The Gateway has already validated the token, so the interceptor decodes its claims without re-verifying the signature. Methods other than `tools/call` need no actor.

It forwards the checked actor ID, the calling trace's ID and the custom headers named in `FORWARD_HEADERS`, and preserves the request body. It never forwards `Authorization`. A refused request gets a JSON-RPC error from the interceptor itself, and an unexpected failure is raised, so a call is never forwarded without its checks ([the contract](#request-interceptor-contract)).

`FORWARD_HEADERS` matching is case-insensitive and preserves incoming casing. It excludes `authorization`, `auth` and `x-aria-actor-id`, so custom forwarding cannot replace the checked actor. Configure only non-credential context headers; this is not a general secret detector. Keep `pass_request_headers=True` on the Gateway interceptor, and keep each header in the targets' `allowedRequestHeaders`. See [identity and tokens](identity-and-tokens.md#custom-headers).

## REQUEST interceptor contract

The interceptor uses a different event/response shape from a target Lambda. A representative request is:

```json
{
  "mcp": {
    "gatewayRequest": {
      "headers": {"Authorization": "Bearer <agent token>", "x-aria-actor-id": "<64 hex>", "x-locale": "ko-KR",
                  "traceparent": "00-<32 hex trace ID>-<16 hex>-01", "x-amzn-trace-id": "Root=1-<8 hex>-<24 hex>;..."},
      "body": {"method": "tools/call", "params": {"name": "saved-items___get_saved_items", "arguments": {"limit": 5}}}
    }
  }
}
```

It returns the request for the target:

```python
{
    "interceptorOutputVersion": "1.0",
    "mcp": {"transformedGatewayRequest": {"headers": headers, "body": body}},
}
```

or, for a request it refuses, the answer itself, which the Gateway returns without calling the target:

```python
{
    "interceptorOutputVersion": "1.0",
    "mcp": {"transformedGatewayResponse": {"statusCode": 200, "body": {
        "jsonrpc": "2.0", "id": request_id,
        "error": {"code": -32003, "message": "Access denied by the Gateway's access check; retrying won't help"},
    }}},
}
```

- The bearer token is the agent's own token, minted by AgentCore Identity; the shopper's sign-in JWT never reaches the Gateway. The Gateway's `CUSTOM_JWT` authorizer has already validated it.
- Every `tools/call` must carry a well-formed `x-aria-actor-id` and a token from the agent, as the flow above describes. Other allowed methods, such as `tools/list`, need no actor.
- The checked actor ID is forwarded as `x-aria-actor-id`, and the calling trace's ID as `x-correlation-id`, as 32 hex characters. It comes from `traceparent`, which the agent's OpenTelemetry instrumentation sends, so it is the agent's own trace; a request without one, such as a direct call, gets the `Root` of the `x-amzn-trace-id` AWS adds, without its `1-` and dashes. The agent's spans in Transaction Search (`aws/spans`) and its Runtime log group carry the same ID, so a target that logs it, as the saved-items sample does, leads an operator from its log line to the agent's turn.
- `Authorization` is never returned. The Gateway would forward an interceptor's `Authorization` to the target in place of the target's own credential. `FORWARD_HEADERS` allows custom context header names, matched case-insensitively with original casing preserved; `Authorization`, `auth`, `x-aria-actor-id` and `x-correlation-id` are blocked through this path.
- Successful transformation preserves `body`.
- A failed check is logged at WARNING as `interceptor: target invocation denied: <reason>`, the line the `InterceptorDenials` alarm counts. The request is answered with a JSON-RPC error for its id: `-32601` (method not found) for a method the agent doesn't use, and `-32003` (access denied) for a failed actor, token or settings check. The reason stays in the log; the agent learns only that the call was refused, so it can tell a refusal from an outage and doesn't retry.
- A refused message with no id to answer (a notification, a batch, a body that isn't a JSON-RPC message), and any unexpected exception, logged with its stack trace, is raised instead. The request is then denied rather than forwarded: the Gateway answers a generic HTTP 500, which the agent's transport turns into a JSON-RPC error for that one call, so the session survives ([identity and tokens](identity-and-tokens.md#the-agents-gateway-token)).
- Gateway's interceptor configuration must set `pass_request_headers=True` to expose inbound headers, and each target's `allowedRequestHeaders` must list the headers it should receive.

Lambda targets read these headers from `context.client_context.custom.bedrockAgentCorePropagatedHeaders`. A target must scope its data by the actor ID and enforce its own authorization. See [identity and tokens](identity-and-tokens.md).
