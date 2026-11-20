# Gateway tools

[한국어](README.ko.md)

This package holds the Lambda functions behind AgentCore Gateway. Product search runs in the agent as a local Strands tool (`packages/agent/src/tools/product_search.py`); its search code, providers and example catalog are in `packages/catalog`, and the job that indexes a catalog into OpenSearch is `packages/indexer`. See [tool placement](../../docs/design-docs/tool-placement.md) for where a tool belongs, [add a local tool](../../docs/guides/add-a-local-tool.md) for writing one, [Gateway onboarding](../../docs/guides/gateway-onboarding.md) for onboarding an API or MCP server to the Gateway, and [identity and tokens](../../docs/design-docs/identity-and-tokens.md) for how identity and tokens flow.

- `saved-items-lambda` is a sample Gateway Lambda target for a tool that needs the shopper's identity: `get_saved_items`. It reads the shopper's actor ID from the `x-aria-actor-id` header the Gateway passes in the Lambda client context, and refuses the call without a valid one. It returns a stable `product.v1` sample per shopper from the example catalog in `packages/catalog`, mapped onto cards through the example's catalog definition. A real implementation would read the shopper's own data, scoped by actor ID. It returns the result object directly; Gateway creates the MCP content envelope. Do not return an API Gateway `statusCode/body` response: AgentCore treats that wrapper as ordinary tool content, including wrapped errors.
- `gateway-interceptor` is a stdlib-only REQUEST interceptor. The agent never forwards the shopper's sign-in token; it calls the Gateway with a token minted for itself and sends `x-aria-actor-id`. The interceptor requires a well-formed actor ID on every tool call and passes it on. For an on-behalf-of token it also checks that the header matches the token's own subject. It never forwards `Authorization`. A failed check denies the call.

Run the unit tests from the repository root:

```sh
(cd packages/mcp-servers/saved-items-lambda && uv run --frozen pytest)
(cd packages/mcp-servers/gateway-interceptor && uv run --frozen pytest)
(cd packages/catalog && uv run --frozen pytest)
```

`pnpm check` runs them with every other suite. See [integration recipes](../../docs/guides/integrations.md) and [the catalog runbook](../../docs/guides/operations.md#catalog-runbook).
