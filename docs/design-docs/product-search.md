# Product search and the tool packages

Status: verified

Product search is a local Strands tool in the agent, because it runs on most turns and needs no per-user authorization; its search code is in `packages/catalog`. The tool's schema, the query and the cards come from the stage's catalog definition, described in [catalog search](catalog-search.md). Tools that need the shopper's identity or credentials of their own sit behind AgentCore Gateway. `packages/mcp-servers` ships the sample Gateway target and the Gateway's REQUEST interceptor ([the interceptor](gateway-interceptor.md)). [Where a tool belongs](tool-placement.md) explains the split. Read [the Lambda target contract](../guides/gateway-onboarding.md#lambda-target-contract) before changing a Gateway handler.

## Code map

| File | Responsibility |
| --- | --- |
| `packages/agent/src/tools/product_search.py` | `ProductSearchTool`, the local `search_products` tool: its spec from the deployment's catalog definition, the product.v1 result, actionable and sanitized errors, the search logs |
| `packages/catalog/src/aria_catalog/definition.py` | The catalog definition: validation, the tool schema, argument checks, the index, pipeline and query bodies, cards and the indexer's product check. Standard library only |
| `packages/catalog/src/aria_catalog/search.py` | `search_products()`: argument checks, provider selection, the hybrid OpenSearch query and its lexical fallback, the cached OpenSearch client |
| `packages/catalog/src/aria_catalog/providers.py` | The fixture provider, the HTTPS adapter and its credential cache |
| `packages/catalog/src/aria_catalog/embedding.py` | Cohere Embed v4 on Bedrock, 96 texts per call, and the search path's short-timeout client |
| `packages/catalog/src/aria_catalog/contract.py` | `product_v1()`: the product check applied to every provider's products |
| `packages/catalog/src/aria_catalog/errors.py` | `CatalogQueryError` (invalid arguments) and `CatalogBackendError` (catalog faults) |
| `packages/catalog/src/aria_catalog/example/` | The example catalog: `catalog.toml` and its 150 synthetic products, `products.json` |
| `packages/indexer/` | The indexing job, a Fargate task in the VPC ([catalog search](catalog-search.md#the-indexing-job)) |
| `saved-items-lambda/src/handler.py` | Sample Gateway Lambda target: tool-name dispatch, actor-ID check, argument allowlist, direct product.v1 result |
| `gateway-interceptor/src/handler.py` | Actor-ID check, on-behalf-of subject match, credential stripping and custom-header allowlist |
| `packages/infra/stacks/gateway_stack.py` | Gateway authorizer, interceptor, sample and customer targets, per-target header allowlists |

Paths without a `packages/` prefix are relative to `packages/mcp-servers`.

## Product-search flow

1. The agent builds `ProductSearchTool` from `CATALOG_DEFINITION`, the definition as JSON (the packaged example when it's empty). An invalid definition fails the session build. The tool's spec is `aria_catalog.tool_spec()`.
2. The model calls `search_products`. The agent's `ToolSafety` hook checks the tool against the allowlist, the per-turn call budget (8 by default) and the 8 KiB argument limit. Strands passes the arguments to the tool as the model sent them.
3. The tool calls `aria_catalog.search_products()`, which checks the arguments once with `canonical_arguments()`, so every provider gets the same values. An unknown parameter, a value outside the declared values, a wrong type, a reversed range, a `query` over 200 characters or a `limit` below 1 raises `CatalogQueryError` with a message the model can act on. A blank value counts as absent; with no criteria, it returns an empty list before creating a query or client. `limit` defaults to 3 and is capped at 20.
4. `SEARCH_PROVIDER` selects the backend:
   - `fixture` searches the example catalog in memory, lexically, and serves only the example definition;
   - `opensearch` embeds the query and sends the hybrid query through the search pipeline, or the lexical query alone when there's no query text or the embedding fails;
   - `http` POSTs the canonical arguments.

   An unknown provider raises `CatalogBackendError` when a search reaches provider selection.
5. Every provider returns products in the catalog's field names. Each maps onto a card through `[catalog.card]` and must pass `product_v1()`: exactly the ten fields, an ID matching the web's citation rule (`[A-Za-z0-9._:-]{1,128}`), a whole-number year and strings of at most 2000 characters. A product that fails is dropped and counted through `on_invalid`; if products came back and none pass, the search raises `CatalogBackendError`. Results are cut to `limit`.
6. The tool returns `aria_catalog.result(products)`: `{"schemaVersion":"product.v1","count":N,"products":[...]}`. A `CatalogQueryError` reaches the model as an actionable tool error, so it can correct the call. Any other exception is logged as `product_search_failed` (provider, error type, duration, and a `CatalogBackendError`'s fixed message) and reported as `Product search failed`, without its details. A finished search logs `product_search_finished`, a fallback `product_search_embedding_failed`, and dropped products `product_search_invalid_product`.

The cached OpenSearch client is imported only when that provider is used. It uses HTTPS on 443, certificate verification, SigV4 from the Runtime role, HTTP compression, a 10-second timeout and one retry. Only the card fields are requested. The query embedding uses its own client: a 1-second connect and 3-second read timeout and no retry, so a slow Bedrock falls back to the lexical search within about 4 seconds.

## HTTPS adapter

`http_search()` reads its endpoint and credential only from the environment and Secrets Manager. The credential is cached for 300 seconds per secret ARN; after a 401 or 403 the adapter fetches it again and retries once. Redirects are refused. It reads at most 256 KiB plus one byte to detect oversize responses, with a 10-second timeout. Every fault raises `CatalogBackendError` with a fixed message, never response content, URLs or credentials.

The request body (`http_body()`) is flat, in the tool's parameter names: `query`, every filter parameter and `limit`. A criterion not given is `null`, and a range is `{"min": …, "max": …}`, with `null` for an open end. The response must hold a list of at most 20 products at `SEARCH_RESULT_PATH` (dotted, `products` by default), in the definition's field names. They then map through `[catalog.card]` and pass the same `product_v1()` check as the other providers': a numeric ID becomes a string and a whole-number year an integer.

## Extending the implementation

### Add a search filter

Add the field to the catalog definition with `filter = true` (and its `values`, for a keyword) or `filter = "range"`. The tool schema, the argument checks, the query and the HTTP body follow from the definition. For `opensearch`, deploy the definition, then index the catalog again as a new version so the mapping has the field ([operations](../guides/operations.md#change-the-catalog-definition)). For `http`, the API must accept the new parameter. Check the definition with the catalog's tests, or with `aria_catalog.load_definition("<file>")` and `tool_spec()`.

The fixture serves only the example definition. A change to the example changes the tool catalog in `docs/generated/` and the example's tests (`test_example.py`).

### Add a local tool or a Gateway target

Follow [add a local tool](../guides/add-a-local-tool.md) for a local tool and [Gateway onboarding](../guides/gateway-onboarding.md) for a Gateway target. A local tool is a Strands tool added to the agent's `_build_tools()` and to the tool allowlist. A Gateway tool is a `gateway.targets` entry (`lambda`, `mcp`, `openapi` or `apigateway`) with explicit `allowedTools`, credentials and a reviewed Cedar policy; there is no need to replace the shared agent/API. Keep backend URLs, credentials, identities and arbitrary query bodies out of model input. Scope IAM to the exact backend actions/resources. Customer Lambda schemas support only the inline keys validated in `gateway_stack.py`; additional constraints belong in the backend. Gateway names tools as `{target}___{tool}`.

### Change the interceptor

Update `checked_actor()`/`_forward_headers()` and the contract, actor and header tests. Preserve the actor requirement on tool calls, the on-behalf-of subject match and Authorization stripping. Do not log header values or tokens. The downstream datastore must still enforce user scope independently.

## Verification

Run the commands in [testing](../guides/testing.md). The root `pnpm check` covers the package suites, real CDK profiles, web product contract and production web build. Deployment is a separate [release procedure](../guides/release.md).
