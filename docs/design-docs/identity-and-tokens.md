# Identity and tokens

Status: verified

This doc covers three things:

- how a tool learns who the shopper is without ever seeing the shopper's token;
- which token the Gateway sees;
- how custom headers travel.

[Where a tool belongs](tool-placement.md) explains the split between local and Gateway tools. [Gateway onboarding](../guides/gateway-onboarding.md) covers putting an API or MCP server behind the Gateway.

Three rules hold throughout:

- **The shopper's sign-in token never leaves the agent.** The Runtime verifies it. The agent reads it only to derive the actor ID and to check that the shopper owns the session, and never forwards it.
- **Gateway calls carry the agent's own token and `x-aria-actor-id`.** Agent code sets both; the model sets neither.
- **Identity is never a model-writable tool argument.** Custom headers are caller input: they can select a locale or narrow a request. They can't prove who the caller is or widen access.

## At a glance

| | Local tool | Gateway target |
| --- | --- | --- |
| Runs in | The agent container | A Lambda function, an API, or a separately hosted MCP server |
| Who the shopper is | `agent.state["user_id"]`, the actor ID | `x-aria-actor-id`, checked by the interceptor against the agent's token: with an on-behalf-of token, also against its subject |
| Credentials | The Runtime's IAM role | The target's own outbound credentials. Never the shopper's token |
| Custom headers | `invocation_state["forwarded_headers"]`, refreshed per invocation | HTTP headers, fixed when the session is built |
| Example | `packages/agent/src/tools/product_search.py` | `packages/mcp-servers/saved-items-lambda/src/handler.py` |

## How a request flows

```
Browser ──shopper JWT──▶ API ──shopper JWT──▶ Runtime (validates the JWT) ──▶ agent
                                                                             │
  local tools (search_products) ◀── in-process ──────────────────────────────┤   the agent never forwards the JWT
                                                                             │
  AgentCore Identity ◀── GetResourceOauth2Token(workload access token) ──────┤
                                                                             │
  AgentCore Gateway ◀── agent's token + x-aria-actor-id ─────────────────────┘
     authorizer (agent tokens only) → interceptor (checks x-aria-actor-id) and Cedar: both must allow → target
```

## The shopper's token at the agent

The Runtime's `CUSTOM_JWT` authorizer verifies the shopper's access token before the container sees it. The agent then:

- reads the token in `app.py::_extract_token()`;
- derives the actor ID in `resolve_actor_id()`. The actor ID is the SHA-256 of the verified issuer, subject and optional trusted tenant claim (`packages/contracts`);
- checks the principal-bound session ID in `require_session_owner()` on every invocation, including cached sessions.

That is all the shopper's token is used for. `runtime_actor()` relies on the hosted authorizer for the signature check, and unit tests don't run that authorizer. Use synthetic contexts in tests, and verify real authorization on a deployed stage.

## The agent's Gateway token

The Runtime sends a workload access token with each request, in the `WorkloadAccessToken` header, and AgentCore Identity binds it to the verified shopper.

- `DownstreamToken` (`packages/agent/src/core/downstream.py`) trades the workload access token for a Gateway token with `GetResourceOauth2Token`. It caches that token until 60 seconds before it expires.
- `GatewayTransport` (`core/transport.py`) asks for the token on every HTTP request, so a refreshed token is used without reconnecting. It sends the token only to the Gateway's own origin and follows no redirects.
- mcp 1.30 closes the whole MCP session on one non-2xx reply or network error. `GatewayTransport` answers those itself: a failed request, or a failed mint, gets a JSON-RPC error for its own id, so only that tool call fails; a failed notification gets 202. Failures are logged as `gateway_http_error`, `gateway_request_failed` and `gateway_token_failed`.
- The Gateway URL must be `https://` on an AgentCore Gateway host, `<id>.gateway.bedrock-agentcore.<region>.amazonaws.com` (`gateway_url_rejected` otherwise; other `amazonaws.com` hosts, which anyone can create, are refused), and the agent skips the Gateway entirely when no Gateway tool is allowed.
- The Runtime role may mint tokens only from the one configured credential provider.

The first mint happens when the session is built, so it adds to the first turn of every session: about 0.75 s on Dev.

Choose the flow with `[gateway.agentAuth]`:

| `flow` | Token | What the Gateway accepts | What targets know about the shopper |
| --- | --- | --- | --- |
| `on_behalf_of` (default) | RFC 8693 delegation: `sub` is the shopper, `act.sub` is the agent's client, `aud` is `aria-tools` | `aud = aria-tools` and scope `aria-tools/invoke`. A shopper's own access token has no `aud`, so it is rejected | `x-aria-actor-id`, which the interceptor checks against the token's own subject |
| `m2m` | Client credentials for the agent's client | That client ID and scope `aria-tools/invoke` | `x-aria-actor-id` only, trusted because only the agent's client can call the Gateway |

With managed Cognito, the Auth stack sets up the on-behalf-of exchange:

- an `aria-tools` resource server with an `invoke` scope;
- the agent's confidential client, with client credentials and `ALLOW_TOKEN_EXCHANGE_DELEGATION`;
- an AgentCore Identity OAuth2 credential provider named `<ns>-gateway`. It is configured for `TOKEN_EXCHANGE`, with the agent's client-credentials token as the actor;
- managed login on the user pool domain. Cognito refuses token exchange on a classic hosted-UI domain;
- `aria-tools/invoke` on the web client's tokens. An exchange can only narrow the shopper's scopes, so the web app must request this one (`OIDC_SCOPES`).

Token exchange needs the Essentials or Plus feature plan. Dev uses Essentials and Prod uses Plus.

The agent sends the shopper's token as `subject_token_type=urn:ietf:params:oauth:token-type:access_token`. Otherwise AgentCore Identity labels it `…:jwt`, which Cognito rejects with HTTP 400. The issued token expires with the shopper's own token, and the exchange never returns a refresh token.

**BYO IdP.** For `on_behalf_of`, the IdP must support RFC 8693 token exchange and accept the subject token typed as an access token. Create the AgentCore Identity credential provider yourself, and name it in the configuration:

```toml
[gateway.agentAuth]
flow = "on_behalf_of"              # or "m2m"
credentialProvider = "shopping-gateway"
scopes = ["shopping:tools"]
audience = "shopping-tools"        # on_behalf_of: the Gateway checks aud
# clientId = "agent-client-id"     # m2m: the Gateway checks this client
```

The web app must request `shopping:tools` as well, for the same narrowing reason.

**External Gateway.** It needs `credentialProvider` and `scopes` too, and its authorizer must accept the token that provider mints.

## What the Gateway checks

The Gateway's `CUSTOM_JWT` authorizer accepts only the agent's token, as the table above describes. Then the REQUEST interceptor and, when enabled, Cedar decide the call; both must allow it. AgentCore doesn't document which runs first, so neither may rely on the other having run. The interceptor is configured with `pass_request_headers=True`. It returns `interceptorOutputVersion: "1.0"` with `mcp.transformedGatewayRequest`:

| Header | Behavior |
| --- | --- |
| `x-aria-actor-id` | Required on every `tools/call`, as exactly 64 lowercase hex characters, with a token that proves the agent sent it. With `on_behalf_of`, `act.sub` must be the agent's client and the header must equal the actor ID derived from the token's own `iss`, `sub` and tenant claim. With `m2m`, `client_id` must be the agent's client. Otherwise the call is denied. Passed to the target |
| `Authorization` | Never returned to the Gateway. The Gateway forwards an interceptor's `Authorization` to the target in place of the target's own credential |
| `x-correlation-id` | The calling trace's ID, as 32 hex characters: from `traceparent`, which the agent's OpenTelemetry instrumentation sends, or else the `Root` of the `x-amzn-trace-id` AWS adds to every request. The agent's spans (`aws/spans`) and Runtime logs carry the same ID, so a target that logs it leads an operator to the agent's turn. Custom forwarding can't replace it |
| Configured custom names | Case-insensitive `FORWARD_HEADERS` match that keeps the incoming casing. `authorization`, `auth` and `x-aria-actor-id` are excluded; the agent also drops any custom header starting with `x-aria-` (`forward_header_dropped`) |

- The interceptor also denies batches and any method other than `initialize`, `ping`, `tools/list`, `tools/call` and `notifications/*`, and every request while `GATEWAY_AUTH_FLOW` or `AGENT_CLIENT_ID` is unset ([the interceptor](gateway-interceptor.md#interceptor-flow)).
- A failed check is answered by the interceptor itself, without calling the target: a JSON-RPC error for that call (`-32003`, access denied), so the agent can tell a refusal from an outage. The reason is only in the interceptor's log, and the `InterceptorDenials` alarm counts it. A refused message with no id to answer, and an unexpected failure, raise instead; the Gateway then answers with a generic HTTP 500 ("An internal error occurred").
- Logs record header names, never values.
- Headers the interceptor returns take precedence over the caller's. A header reaches a target only if that target's `allowedRequestHeaders` lists it.
- The interceptor uses only the standard library. Its copy of the actor-ID derivation is tested against reference values from `aria_contracts`.

## Custom headers

Declare non-credential headers once:

```toml
[gateway]
forwardHeaders = ["x-tenant-id", "x-locale"]
```

CDK adds them to:

- the Runtime's request allowlist;
- the agent's and the interceptor's `FORWARD_HEADERS`;
- each target's `allowedRequestHeaders`, after `x-aria-actor-id` and `x-correlation-id`, with at most 10 per target, so `forwardHeaders` names at most 8.

The loader rejects `Authorization`, `auth`, `x-correlation-id` and any `x-aria-*` name, in any casing, and the agent refuses to start with one in `FORWARD_HEADERS`. Credentials never travel as custom headers, and `x-aria-*` is the agent's own namespace: a forwarded header carries the caller's value, so a shopper could otherwise set one for local tools' `invocation_state`. AgentCore also refuses to propagate some names, such as `Cookie`, `X-Forwarded-For` and `X-Amzn-*` ([header propagation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-headers.html)).

The chain starts at the Runtime request, not at the storefront. `packages/api/src/runtime_client.py` forwards authorization and the Runtime session ID but no custom headers, and the API's CORS allows only `Authorization` and `Content-Type`. Sending `x-locale` to the shipped browser and API therefore doesn't reach tools. That needs a direct Runtime caller, or an API forwarding path you add and test.

**Local tools** get the current values on each invocation. `SessionBuilder._init_agent_state()` stores the verified actor ID as `user_id`, and each invocation calls:

```python
_session.stream_async(prompt, invocation_state={"forwarded_headers": forward_headers})
```

A tool declares `context=True` to read both through `ToolContext`:

```python
from strands import ToolContext, tool

@tool(context=True)
def my_tool(tool_context: ToolContext, limit: int = 10) -> dict:
    actor_id = tool_context.agent.state.get("user_id")
    if not actor_id:
        raise ValueError("Verified user_id required")
    headers = tool_context.invocation_state.get("forwarded_headers") or {}
    locale = headers.get("x-locale", "en-US")
    # Query a backend scoped to actor_id; headers may only narrow that scope.
    ...
```

Register the tool in `SessionBuilder._build_tools()` and add its name to `Config.local_tools`. Never put a raw bearer token in model context. Product search needs neither identity nor headers, so it reads neither.

**Gateway targets** get the headers as HTTP request headers. A Lambda target reads them from its client context:

```python
def propagated_headers(context):
    custom = getattr(getattr(context, "client_context", None), "custom", None) or {}
    return custom.get("bedrockAgentCorePropagatedHeaders", {}) or {}

def lambda_handler(event, context):
    headers = {key.lower(): value for key, value in propagated_headers(context).items()}
    actor_id = headers.get("x-aria-actor-id")   # checked by the interceptor
    locale = headers.get("x-locale", "en-US")
    # Scope every query to actor_id, and refuse the call without it.
    ...
```

The selected tool is in `bedrockAgentCoreToolName` and the headers in `bedrockAgentCorePropagatedHeaders`; neither is a model argument. The values are copied when the session's MCP connection is built. A later change of locale therefore reaches local tools, but not Gateway calls in the same session.

Don't declare these headers as parameters in an OpenAPI document or a REST API. The Gateway can't propagate a header that the schema declares, and a declared parameter is an argument the model fills in. The loader rejects an OpenAPI `schemaFile` that declares one.

## Cedar and data authorization

Cedar decides whether a tool action is allowed:

- **With an on-behalf-of token,** the principal is the shopper: `AgentCore::OAuthUser` with the shopper's `sub`. Claims are available as tags.
- **With M2M,** every call has the agent's principal, so policies can only distinguish tools and arguments.
- **Context** holds the tool arguments (`context.input`), not headers.

Cedar authorizes the action, not the rows. Target code and its datastore must scope data to the actor ID themselves. Inspect `packages/infra/components/policies/`, and test denials as well as successes.

## Verify

Locally, from the repository root:

```sh
(cd packages/mcp-servers/gateway-interceptor && uv run --frozen pytest)
(cd packages/agent && uv run --frozen pytest)
(cd packages/infra && uv run --frozen pytest tests/test_forward_headers.py tests/test_gateway_identity.py tests/test_customer_targets.py)
```

These cover:

- **Interceptor:** the actor checks for both token kinds, credential stripping, and header forwarding.
- **Agent:** the token source's caching and failures, and a check that the shopper's JWT never reaches the Gateway transport.
- **Infra:** the authorizer for each flow, the header allowlists, and the rejection of credentials and declared headers in configuration.

`pnpm check` runs all of them, and no active `.toml` needs to be overwritten.

On a deployed stage, check these:

| Check | Expected |
| --- | --- |
| A Gateway tool call through the agent | Succeeds |
| A Gateway call with the shopper's own token | 403: the token is validly signed but has no `aud` |
| A Gateway call without a token | 401 |
| The minted on-behalf-of token | `sub` is the shopper, `act.sub` is the agent's client, `aud` is `aria-tools`, scope is `aria-tools/invoke` |
| A `tools/call` whose `x-aria-actor-id` names someone else, or is missing | Denied by the interceptor |

`scripts/smoke.py` checks a basic conversation, not these. If you use custom headers, also test values that change across turns. Build containers with the `packages/` context described in [container builds](../guides/release.md).

## Security checklist

- [ ] Identity is not a model-writable parameter, and session ownership is checked on every turn.
- [ ] The shopper's token never leaves the agent, and the Gateway rejects it.
- [ ] The interceptor never returns `Authorization`, and targets authenticate the Gateway with their own credentials.
- [ ] Custom context never widens the verified user's scope or carries credentials.
- [ ] Local tools fail on a missing actor ID and are explicitly allowlisted.
- [ ] Gateway targets require `x-aria-actor-id`, scope their data to it, and don't declare it as a parameter.
- [ ] Each required hop is implemented and allowlisted, including any custom API forwarding.
- [ ] Backend queries independently enforce user and tenant scope.
- [ ] Logs exclude token and header values, and tests cover denials and refreshed credentials.
