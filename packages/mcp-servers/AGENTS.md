# packages/mcp-servers

The Gateway's Lambda functions, which are plain handlers behind AgentCore Gateway rather than MCP servers:

- `gateway-interceptor`, the REQUEST interceptor;
- `saved-items-lambda`, the sample target.

Product search runs in the agent (`../catalog`, `../agent/src/tools/product_search.py`), and catalog indexing in `../indexer`.

## Rules

- The Gateway accepts only the agent's own token.
  - The interceptor requires a well-formed `x-aria-actor-id` on every `tools/call`. For an on-behalf-of token, it must match the token's `iss`, `sub` and tenant.
  - It never returns `Authorization`.
  - Keep its `actor_id` identical to `../contracts`, and keep it stdlib-only.
- Targets return the result object directly (`product.v1` for products); the Gateway builds the MCP envelope. Never return an API Gateway `statusCode`/`body` wrapper. Raise sanitized exceptions for failures.
- Model input never chooses a backend URL, credentials, an identity or a raw query. Validate sizes, types and limits.
- Headers reach a target only through the Gateway's `pass_request_headers=True` and the target's `allowedRequestHeaders`. They're context, never tool arguments.
- Reuse cached clients, and keep timeouts and concurrency bounded. Errors contain no secrets.

## Commands

- `(cd saved-items-lambda && uv run --frozen pytest)`, `(cd gateway-interceptor && uv run --frozen pytest)`. Tests never call AWS.

## Read

- [The Gateway interceptor](../../docs/design-docs/gateway-interceptor.md), [Gateway onboarding](../../docs/guides/gateway-onboarding.md)
- [Gateway notes](../../docs/references/agentcore-gateway-notes.md), [catalog runbook](../../docs/guides/operations.md#catalog-runbook)
