# packages/catalog

Catalog search driven by a catalog definition, shared by the agent's local search tool, the indexer and the saved-items sample:

- the definition, in `src/aria_catalog/definition.py`: validation, the tool schema, argument checks, the index, pipeline and hybrid query bodies, cards, the indexer's product check. Standard library only, so the infra loader imports it at synth;
- Cohere Embed v4 on Bedrock, in `embedding.py`;
- providers, in `search.py` and `providers.py`;
- the product.v1 product check, in `contract.py`, and the two error types, in `errors.py`;
- the example catalog, `example/catalog.toml` and its 150 synthetic products, `example/products.json`, which the fixture provider searches and the saved-items sample draws from.

## Rules

- The example catalog is synthetic, not customer inventory. `test_example.py` checks every product against the definition. The web tests' `packages/web/tests/fixtures/products.json` holds the cards of its first three products (`sample-1` to `sample-3`); change it with them.
- The tool schema comes from the definition: an enum for each keyword filter, its declared values. The model never writes a query; `search_body()` builds it from validated arguments.
- Model input never chooses a backend URL, credentials, an identity, an index, a field or a raw OpenSearch query. Validate sizes, types and limits, and refuse HTTPS redirects.
- Invalid input raises `CatalogQueryError` with a message the model can act on; it's the only catalog error the model sees. A backend, response or settings fault raises `CatalogBackendError`, whose fixed message is logged, so never put values, URLs or secrets in it. An invalid definition raises `DefinitionError`.
- Results are `product.v1`: `schemaVersion`, `count`, `products`. Every provider's products map through `[catalog.card]` and pass `product_v1()`; failing ones are dropped and counted.
- `check_product()` problems name fields, never values.

## Commands

- `uv run --frozen pytest`. Tests never call AWS: Bedrock and OpenSearch are faked.

## Read

- [Catalog search design](../../docs/design-docs/catalog-search.md), [bring your own catalog](../../docs/guides/integrations.md#bring-your-own-catalog)
- [Product search design](../../docs/design-docs/product-search.md), [search spec](../../docs/product-specs/product-search.md)
- [Tool catalog](../../docs/generated/tool-catalog.md), [catalog operations](../../docs/guides/operations.md#catalog-runbook)
