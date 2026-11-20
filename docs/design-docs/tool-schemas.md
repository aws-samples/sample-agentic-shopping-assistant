# Tool schemas

Status: verified

The model picks tools and arguments from the schemas they advertise, so a schema is part of the tool's contract. ARIA declares schemas in three places:

| Tool | Schema | Validated by |
| --- | --- | --- |
| `search_products` (local) | `tool_spec()` in `packages/catalog/src/aria_catalog/definition.py`, generated from the stage's catalog definition; `ProductSearchTool` in `packages/agent/src/tools/product_search.py` advertises it | `canonical_arguments()` in `aria_catalog` |
| `saved-items___get_saved_items` (Gateway sample) | `_create_saved_items_target()` in `packages/infra/stacks/gateway_stack.py` | `saved-items-lambda/src/handler.py` |
| Customer Lambda targets | `toolSchema` in the target's `gateway.targets` entry | The customer's Lambda |

MCP, OpenAPI and API Gateway targets take their tool definitions from the server, the OpenAPI document or the `operations` list. [tool placement](tool-placement.md) explains where a tool belongs, and [Gateway onboarding](../guides/gateway-onboarding.md) how to onboard each target kind.

## Rules for every schema

- Give the tool a unique lowercase verb/noun name such as `search_products`, a description that says when to use it, and an object input schema. The Gateway exposes its tools as `{target}___{tool}`; local tools keep their own names.
- Mark only truly mandatory fields as `required`.
- Never put an identity, credential, backend URL or query body in a schema. Targets learn who the shopper is from `x-aria-actor-id`, which the agent sets in code.
- Descriptions guide the model but enforce nothing. Implement defaults, bounds, shape checks and authorization in code and policy.
- Use `integer` for whole numbers. On the Gateway, Cedar maps `integer` to `Long`, which the shipped policies compare (`context.input.limit <= 20`). `number` maps to `Decimal`, which cannot use Cedar's integer comparison operators.
- Keep model-facing descriptions and enum values in English, so examples show the actual wire contract.

## The local search schema

The schema comes from the catalog definition ([catalog search](catalog-search.md)), so each stage's tool has its catalog's filters. With the example definition (`packages/catalog/src/aria_catalog/example/catalog.toml`):

| Parameter | Advertised type | From the definition | Handling in `aria_catalog` |
| --- | --- | --- | --- |
| `query` | `string`, `maxLength` 200 | Always present | Trimmed; a `multi_match` over the search fields (`title` boost 3, `description`) and, with OpenSearch, the vector query |
| `season` | `string`, `enum` | A keyword filter, `season` | Matched ignoring case, returned in its declared spelling, then a `term` on `season` |
| `gender` | `string`, `enum` | A keyword filter, `gender` | As `season`, on `gender` |
| `category1` | `string`, `enum` | A keyword filter on `category`, with `parameter = "category1"` | As `season`, on `category` |
| `category2` | `string`, `enum` | A keyword filter on `subcategory`, with `parameter = "category2"` | As `season`, on `subcategory` |
| `year` | `integer` | An exact integer filter, `year` | A whole number, or a string or float that spells one, then a `term` on `year` |
| `usage` | `string`, `enum` | A keyword filter, `usage` | As `season`, on `usage` |
| `limit` | `integer`, `minimum` 1, `maximum` 20, `default` 3 | Always present | 3 when absent, capped at 20; below 1 is an error |

- A keyword filter lists its declared values twice: as a JSON Schema `enum`, and in the description ("One of: ..."), after the definition's description. `category2`'s description adds the hint "Jackets and coats are Topwear; sneakers, running shoes and boots are Shoes."
- A range filter (`filter = "range"`) is an object with `min`, `max` or both, and no other keys. A boolean filter is a `boolean`, and a float filter a `number`.
- `canonical_arguments()` checks every argument, since Strands passes them to the tool as the model sent them. An unknown parameter, a value outside the declared values, a wrong type, a range whose `min` exceeds its `max`, a `query` over 200 characters or a `limit` below 1 raises `CatalogQueryError`, whose message names the parameter and lists what's accepted. The tool returns that message to the model as an error so it can correct the call.
- `required` is empty: any single criterion is a valid search, and no criteria returns an empty result without a backend call.
- The tool's description is the definition's `description`, then "Prefer the filters to free text, in this order: season, gender, category1, category2, year, usage. Use query only for what they leave out."
- Cedar does not see local tool calls. `ToolSafety` bounds them instead: the tool allowlist, the per-turn call budget and the 8 KiB argument limit.

The generated schema, exactly as the model sees it, is in the [tool catalog](../generated/tool-catalog.md#search_products). `ProductSearchTool` builds it once, when the session is built:

```python
class ProductSearchTool(AgentTool):
    def __init__(self, definition: Optional[CatalogDefinition] = None) -> None:
        super().__init__()
        self.definition = definition if definition is not None else aria_catalog.definition_from_env()
        self._tool_spec: ToolSpec = aria_catalog.tool_spec(self.definition)
```

## Gateway schemas

The saved-items sample declares one tool:

```python
agentcore.CfnGatewayTarget.ToolDefinitionProperty(
    name="get_saved_items",
    description="The shopper's saved items (wishlist). Use it when the shopper asks about items they saved.",
    input_schema=agentcore.CfnGatewayTarget.SchemaDefinitionProperty(
        type="object",
        properties={
            "limit": agentcore.CfnGatewayTarget.SchemaDefinitionProperty(
                type="integer", description="How many saved items to return (default 5, max 20)."
            ),
        },
        required=[],
    ),
)
```

It has no shopper parameter: the handler reads `x-aria-actor-id` and accepts only `limit`.

A customer Lambda target declares `toolSchema` as a list of `{name, description, inputSchema}` tables, and every name in `allowedTools` must appear in it:

```toml
[[gateway.targets]]
name = "wishlist"
kind = "lambda"
lambdaArn = "arn:aws:lambda:<region>:<account>:function:wishlist"
allowedTools = ["list_wishlist"]
policyFile = "components/policies/wishlist.cedar"

[[gateway.targets.toolSchema]]
name = "list_wishlist"
description = "The shopper's wishlist. Use it when the shopper asks about items they saved."

[gateway.targets.toolSchema.inputSchema]
type = "object"
required = []

[gateway.targets.toolSchema.inputSchema.properties.limit]
type = "integer"
description = "How many items to return (max 20)."
```

The converter in `gateway_stack.py` accepts only `type`, `description`, `properties`, `required` and `items`, nested as needed. Any other keyword, such as `enum`, `minimum`, `maximum`, `default` or `pattern`, fails synthesis; enforce those constraints in the target. A tool without a description is advertised as "Customer tool". This is the repository's inline-schema contract, not a limit of JSON Schema itself.

## Checklist

- When you change the catalog's filters or their values, change the catalog definition: the schema, the argument checks and the query are generated from it. Update a description that names a changed value, such as the `category2` hint, and index the catalog again as a new version so the index has the values ([operations](../guides/operations.md#change-the-catalog-definition)).
- Keep results bounded and return the versioned object directly. The web renders product cards only for `product.v1` with valid product fields; the shopper never sees other tool output, only what Aria writes about it.
- For a Gateway tool, give it explicit `allowedTools`, backend authorization and a reviewed Cedar policy. Tool discovery is not permission to call a tool.

Run:

```sh
(cd packages/catalog && uv run --frozen pytest)
(cd packages/agent && uv run --frozen pytest tests/unit/test_product_search_tool.py)
(cd packages/mcp-servers/saved-items-lambda && uv run --frozen pytest)
(cd packages/infra && uv run --frozen pytest tests/test_gateway_stack.py)
```

The agent tests check that the spec is generated from the definition, under `inputSchema.json` as Converse takes it, lists every declared value as an enum and follows a custom definition, and that an invalid definition fails when the tool is built. The catalog tests check the example's schema against a snapshot and that every declared value is accepted. The infra test checks the sample's advertised schema. `pnpm check` runs all of these.

