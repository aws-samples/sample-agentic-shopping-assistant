# Integration contracts

[한국어](integrations.ko.md)

## Configuration and ownership

Set `ARIA_CONFIG` to an explicit TOML file; otherwise CDK reads gitignored `packages/infra/.toml`. Configuration is validated at synthesis. CDK injects corresponding environment variables into API and Runtime. Unknown keys and contradictory provider selections fail validation. Every key, its default and its bounds are in the [configuration reference](../generated/config-reference.md).

The fixed agent and AgentCore Memory remain maintained by this project. Managed Cognito, Gateway and optional OpenSearch resources are created only for selected integrations. Existing Gateway/IdP/search endpoints and credential providers remain customer-owned. Creating a target registers a connection; it does not deploy the remote MCP server or alter its IdP. Every stack and resource name derives from `app.ns` and `app.stage`, so changing either creates a new set of stacks; the old ones and their data stay deployed. Turning an integration off, or replacing a managed one with your own, leaves its stack (`<ns>Auth`, `<ns>Opensearch`, `<ns>Gateway` or `<ns>Evaluations`) out of the CDK app, and a deploy never deletes a stack the app no longer contains: keep, import or delete it and its resources deliberately.

## Identity

Managed Cognito uses an authorization-code client with PKCE, the `aria/chat` and `aria-tools/invoke` access scopes, and managed login. A second, confidential client belongs to the agent. It is used only for the token exchange that gives the agent its Gateway token ([identity and tokens](../design-docs/identity-and-tokens.md)). Multi-factor sign-in with an authenticator app (TOTP) follows `auth.mfa` in every stage. With `optional`, the default, a shopper who has registered an authenticator is asked for a code; managed login doesn't offer enrolment, and the web app has no enrolment screen. `required` makes every shopper register an authenticator at their next sign-in and enter a code each time. `off` turns MFA off. A deployed pool can switch between them. Prod also turns on Cognito Plus threat protection, for which Cognito advises `optional`. Account recovery is by email only. The sign-in domain is `<ns>-<account>.auth.<region>.amazoncognito.com` unless `auth.domainPrefix` names another prefix; choose it before the first deploy, because a user pool's prefix can't be changed in place. Web code contains no client secret and uses access tokens, not ID tokens, for the API. Browser tokens/state use sessionStorage; logout redirects through the IdP. The app ships a Content-Security-Policy meta tag for images, plugins and `<base>`; set `frame-ancestors` and any stricter policy at the web host ([FRONTEND.md](../FRONTEND.md#content-security-policy)). The web's own settings, including `NUXT_PUBLIC_CSP_IMG_SRC` and `NUXT_PUBLIC_REQUEST_TIMEOUT_SECONDS`, are in [FRONTEND.md](../FRONTEND.md#configuration).

For an existing OIDC provider:

```toml
[auth]
provider = "oidc"
issuer = "https://identity.example.com/"
clientId = "shopping-web"
audiences = ["shopping-api"]
scopes = ["shopping:chat"]
callbackUrls = ["https://shop.example.com/auth/callback"]
logoutUrls = ["https://shop.example.com/"]
# tenantClaim = "tenant_id"   # optional; must be issued by the trusted IdP
```

Preserve the exact issuer, including any trailing slash. Register a public authorization-code/PKCE client and exact redirect/logout URLs at the IdP. Configure JWT access tokens, HTTPS OIDC discovery/JWKS, RS256 or ES256 signing, and an API scope/audience. For providers using `client_id` instead of `aud` (including Cognito access tokens), configure `allowedClients` and leave `audiences` empty. When both are configured the API requires both constraints. One issuer is supported per deployment. SAML-only/opaque-token systems need upstream federation or a compatible JWT broker.

With Gateway tools, also configure `[gateway.agentAuth]`. The agent exchanges the shopper's token for its own Gateway token, so the IdP must support RFC 8693 token exchange, or you use `m2m`. The web app must request the tools scope too ([identity and tokens](../design-docs/identity-and-tokens.md#the-agents-gateway-token)). With a managed Gateway, `agentAuth.clientId` is required in both flows: the interceptor refuses any tool call whose token wasn't issued to that client. It can't be one of the shopper's clients, and an on-behalf-of `audience` can't be one of `auth.audiences`, or the shopper's own tokens would pass the Gateway.

Web environment (public configuration only):

```dotenv
API_URL=https://api.example.com
AUTH_PROVIDER=oidc
OIDC_ISSUER=https://identity.example.com/
OIDC_CLIENT_ID=shopping-web
OIDC_SCOPES=openid profile email shopping:chat
# With Gateway tools (on_behalf_of), add the tools scope: ... shopping:chat shopping:tools
OIDC_REDIRECT_URI=https://shop.example.com/auth/callback
OIDC_LOGOUT_URI=https://shop.example.com/
BRAND_NAME=NORDLY
```

For a statically generated Nuxt site these values are applied at build time. Rebuild when they change. Keep issuer/client/scope settings aligned with CDK; the browser may additionally request `openid profile email`. The API and Runtime receive the shopper's access token; the Gateway never does. Signature, expiry, issuer, recipient and scope checks fail closed. The Runtime independently checks session ownership even when invoked directly.

## Catalog providers

```toml
[tools.productSearch]
provider = "fixture"    # fixture | opensearch | http | disabled
# catalogFile = "catalog.toml"   # your catalog definition; empty for the packaged example
```

Product search is a local tool in the agent ([tool placement](../design-docs/tool-placement.md)), so whichever provider you pick runs in the Runtime, which always runs in the VPC's private subnets.

- **`fixture`:** the packaged example catalog, 150 synthetic products searched in memory. It is not customer inventory, so Prod rejects it, and it takes no `catalogFile`. The Dev template selects it.
- **`opensearch`:** your catalog in an OpenSearch 3.5 domain that this app creates in the same VPC, reachable only from there, with a private catalog bucket and an indexing task. The Runtime's security group may reach the domain on port 443. You load the catalog with `scripts/index_catalog.sh` ([operations](operations.md#catalog-runbook)).
- **`http`:** your own HTTPS catalog API. The Prod template selects it with a placeholder `endpoint`.
- **`disabled`:** no search tool.

The HTTP and fixture modes create no domain.

## Bring your own catalog

With `opensearch` you bring two files: the catalog, as a JSON array or as JSONL with one product per line, and a catalog definition in TOML. With `http` you bring the definition, and your API serves the products. The definition says which fields exist, which become the search tool's filters, which are searched as text, what text is embedded and how a product maps onto a card. The search tool's parameters, the OpenSearch index, the hybrid query and the request to an HTTP catalog all come from it ([catalog search](../design-docs/catalog-search.md)).

1. Copy the example definition, `packages/catalog/src/aria_catalog/example/catalog.toml`, next to your stage's `.toml`, and edit it for your fields.
2. Set `tools.productSearch.catalogFile` to it, relative to the `.toml`, and select `opensearch` or `http`. The loader validates the definition at synth, so `scripts/plan_deployment.sh` fails on a mistake and names the key and the rule.
3. Deploy. For `opensearch`, index the catalog ([operations](operations.md#index-the-catalog)); for `http`, point `endpoint` at your API.

The example, its 150 products and what the shopper gets from them are described in the [product search spec](../product-specs/product-search.md#the-example-catalog).

### The definition file

A complete definition, for a catalog with a price range:

```toml
[catalog]
name = "Acme"
description = "Search products in the Acme catalog. Use this tool only for searching products."
idField = "sku"
analyzer = "nori"

[[catalog.fields]]
name = "sku"
type = "keyword"

[[catalog.fields]]
name = "title"
type = "text"
search = true
boost = 3

[[catalog.fields]]
name = "summary"
type = "text"
search = true

[[catalog.fields]]
name = "department"
parameter = "category"
type = "keyword"
filter = true
values = ["Apparel", "Footwear", "Accessories"]
description = "Top-level category"

[[catalog.fields]]
name = "audience"
type = "keyword"
filter = true
values = ["Men", "Women", "Kids"]
description = "Who the product is for"

[[catalog.fields]]
name = "price"
type = "float"
filter = "range"
description = "Price in KRW"

[[catalog.fields]]
name = "line"
type = "keyword"

[[catalog.fields]]
name = "kind"
type = "keyword"

[[catalog.fields]]
name = "colour"
type = "keyword"

[[catalog.fields]]
name = "season"
type = "keyword"

[[catalog.fields]]
name = "year"
type = "integer"

[[catalog.fields]]
name = "occasion"
type = "keyword"

[catalog.embedding]
text = "{title}. {summary}"

[catalog.card]
id = "sku"
productDisplayName = "title"
gender = "audience"
masterCategory = "department"
subCategory = "line"
articleType = "kind"
baseColour = "colour"
season = "season"
year = "year"
usage = "occasion"
```

Its search tool takes `query`, `category`, `audience`, `price` (an object with `min`, `max` or both) and `limit`. Every key, with the rule `parse_definition()` holds it to (`packages/catalog/src/aria_catalog/definition.py`):

| Key | Required | Rule |
| --- | --- | --- |
| `catalog.name` | yes | Non-blank text. Names the catalog in the indexing manifest |
| `catalog.description` | yes | Non-blank text. The search tool's description begins with it |
| `catalog.idField` | yes | A defined `keyword` or `integer` field: the product ID |
| `catalog.analyzer` | no | `standard` (the default) or `nori`, for Korean, on every `text` field |
| `fields[].name` | yes | `[A-Za-z_][A-Za-z0-9_]*`, at most 64 characters, unique. Not starting with `_`, and not `embedding`, the index's vector field |
| `fields[].type` | yes | `keyword`, `text`, `integer`, `float` or `boolean` |
| `fields[].filter` | no | `true` for an exact filter, on any type but `text`; `"range"` for a minimum and maximum, on `integer` or `float`. At most 12 filters |
| `fields[].values` | with a keyword filter | Only, and always, on a `keyword` field with `filter = true`: 1 to 100 values, each non-blank, without surrounding spaces, at most 100 characters and different from the others ignoring case |
| `fields[].search` | on at least one field | `true` on a `text` or `keyword` field: searched as text |
| `fields[].boost` | no | Greater than 0 and at most 10, default 1. Only with `search = true` |
| `fields[].description` | no | The tool parameter's description |
| `fields[].parameter` | no | Only on a filter: the tool parameter's name, the field name by default. Same pattern as `fields[].name`, unique among the filters, and not `query` or `limit` |
| `embedding.text` | yes | A template with at least one `{field}` placeholder, each naming a defined field, with no format spec or conversion. Write a literal brace as `{{` or `}}` |
| `embedding.model` | no | `global.cohere.embed-v4:0` (the default), another Embed v4 inference profile such as `us.cohere.embed-v4:0`, or `cohere.embed-v4:0` |
| `card.*` | yes | Every product.v1 field, each naming a defined field (below) |
| `search.k` | no | Vector neighbours per search, 1 to 100, default 50 |

`fields[]` is a `[[catalog.fields]]` entry, and `embedding`, `card` and `search` are `[catalog.embedding]`, `[catalog.card]` and `[catalog.search]`. An unknown key anywhere is an error.

Every field is indexed, and only the card's fields come back from a search: a field such as `price` can filter, but the shopper never sees it. Each product is checked when it's indexed: a value must have its field's type, a keyword filter's value must be one of its `values`, the ID must be ASCII letters, digits and `._:-`, at most 128 characters, and unique, every card field must be present, and the embedding text can't be blank. A field outside the card may be missing.

### The card

`[catalog.card]` maps each of the ten product.v1 fields, which the web renders as a card ([product cards](../product-specs/product-cards.md)), onto one of your fields:

| Card field | On the card | Your field |
| --- | --- | --- |
| `id` | The citation ID | The `idField` |
| `productDisplayName` | The product name; never blank | `keyword` or `text` |
| `masterCategory`, `season` | The line above the name | `keyword` or `text` |
| `subCategory`, `articleType` | The category path, `subCategory › articleType` (one, when they're equal) | `keyword` or `text` |
| `baseColour` | The colour, with its swatch | `keyword` or `text` |
| `gender`, `usage` | Two labels beside the colour | `keyword` or `text` |
| `year` | The year, on the colour swatch | `integer`, or a `keyword` holding a whole number |

Every card field is required, and several may name the same field. Text values are at most 2,000 characters. A product missing a card field is rejected when it's indexed and dropped when a search returns it. The card fields are fashion-shaped, so a catalog of anything else still names a field for each ([tracker](../exec-plans/tech-debt-tracker.md)).

### Provider contracts

- **`fixture`:** the example definition and products only. Every filter must hold; each `query` word found in a search field, ignoring case, scores that field's boost. No embedding.
- **`opensearch`:** the indexing job loads your catalog into `<alias>-<version>` behind the alias `openSearchIndex` (default `products`). A search with a `query` runs a hybrid query: keyword matching over the search fields with their boosts, and the nearest `k` products by embedding, both with the filters, fused by reciprocal rank fusion. A search without a `query` is the filters alone. If the query can't be embedded, the search runs by keyword and logs `product_search_embedding_failed`.
- **`http`:** your API, below.

HTTP adapter:

```toml
[tools.productSearch]
provider = "http"
endpoint = "https://catalog.example.com/search"
catalogFile = "catalog.toml"
resultPath = "products"
# secretArn = "arn:aws:secretsmanager:REGION:ACCOUNT:secret:catalog-..."
```

The adapter POSTs a JSON object, flat, in the tool's parameter names: `query`, every filter parameter and `limit` (1 to 20). A criterion the model didn't give is `null`, and a range is `{"min": …, "max": …}` with `null` for an open end. Keyword values arrive in their declared spelling. For the definition above:

```json
{"query": "가벼운 러닝화", "category": "Footwear", "audience": null, "price": {"min": null, "max": 100000}, "limit": 3}
```

The response must be JSON with a list of at most 20 products at `resultPath`, a dotted path such as `data.items` (default `products`). Each product is an object in your definition's field names, as you'd index it:

```json
{"products": [{"sku": "A-1001", "title": "경량 러닝화 Lightweight Running Shoes", "audience": "Men", "department": "Footwear", "line": "Shoes", "kind": "Sports Shoes", "colour": "Grey", "season": "Spring", "year": 2025, "occasion": "Sports"}]}
```

The tool maps each product through `[catalog.card]` and wraps the cards in `schemaVersion: product.v1`, `count`, `products`. A product that doesn't make a card is dropped and counted; a response with products but no valid card, a response that isn't JSON, and a missing list are errors, never a successful empty match. Your API applies the filters and the ranking.

Optional Secrets Manager value: `{"header":"x-api-key","value":"..."}` or an explicit `Authorization` header value. The Runtime role may read only that secret; caller tokens and tool-supplied URLs are never used for this HTTP request. Redirects are refused, the request timeout is 10 seconds, and responses are limited to 256 KiB/20 products. The Runtime calls the endpoint from the VPC: a public HTTPS endpoint through the VPC's NAT gateway, a private API over a route from the VPC's private subnets (use an existing VPC with `vpc.vpcId` for that). Otherwise put the catalog behind the Gateway as a customer target and disable local search.

### Korean text, embeddings and residency

- **Nori:** `analyzer = "nori"` analyzes every `text` field with OpenSearch's Nori Korean analyzer, for both the index and the query. Nori is an optional Amazon OpenSearch Service package, offered per region and engine version. Preflight checks that your region offers it for OpenSearch 3.5, and the stack associates it with the domain. Where it isn't offered, use `standard`.
- **Embed v4:** with `opensearch`, the indexing job embeds each product's `[catalog.embedding].text` with Cohere Embed v4 (`input_type` `search_document`), and the search tool embeds the shopper's query (`search_query`), as 1024 floats. Enable model access for the account first; preflight checks the inference profile is `ACTIVE` and the model authorized. The `fixture` and `http` providers make no embedding call.
- **The `global` profile and residency:** the default model, `global.cohere.embed-v4:0`, is a global cross-Region inference profile, which can process a request in any commercial region. In ap-northeast-2, Embed v4 is offered only through the `global` profile, with no in-region model or geographic profile, so the catalog's embedding text and every shopper's query can be processed outside Korea. Decide with your data-residency owner before choosing `opensearch` there. Where a geographic profile such as `us.cohere.embed-v4:0` is offered, set `embedding.model` to keep requests in that geography ([Embed v4 notes](../references/bedrock-embed-v4-notes.md#data-residency)).

### Limits

| Limit | Value |
| --- | --- |
| Filters | 12, each with at most 100 values of at most 100 characters |
| `query` | 200 characters |
| `limit` | 3 by default, at most 20 |
| `search.k` | 100 |
| The definition, as the compact JSON the Runtime gets in `CATALOG_DEFINITION` and the values after it | 16 KB: 8 environment values of at most 2,048 bytes, the most AgentCore Runtime's CloudFormation resource takes in one; synth fails above it. The example takes about 2,400 characters; long value lists and descriptions take the most |
| Card text and IDs | 2,000 characters per text field; IDs of ASCII letters, digits and `._:-`, at most 128 characters |

The tool schema carries `enum`, `maxLength`, `minimum`, `maximum` and `default`. Descriptions guide the model, and `aria_catalog` enforces every rule in code ([tool schemas](../design-docs/tool-schemas.md)).

## Gateway targets and modes

Customer APIs, MCP servers and Lambda functions are onboarded as Gateway targets under `[[gateway.targets]]`. [Gateway onboarding](gateway-onboarding.md) covers:

- each target kind, with its credentials and Cedar policy;
- using an existing Gateway (`gateway.mode = "external"`);
- running without one (`"disabled"`).

Targets never receive the shopper's token. They get `x-aria-actor-id`, and the Gateway authenticates to them with their own credentials.

- **Policies.** Prod managed Gateways require `policy.enabled = true` and `policy.mode = "ENFORCE"`, and every customer target needs a `policyFile`. Shadow-test policies in Dev: an empty or untested policy is not production authorization.
- **Exposed tools.** Only explicitly listed `allowedTools` are exposed, so a new tool needs a configuration and policy change.
- **Outputs.** Product cards are the only tool output the shopper sees, and they need valid `product.v1` results; no other tool output reaches the browser.
- **Scope.** This sample doesn't implement checkout or other write workflows.

## Memory

Only AgentCore Memory is supported, and every deployment has it. Each turn is written as conversation events (short-term memory), so a conversation survives a Runtime session that idled out or reached its lifetime, and the session list and history always work. Namespace identity is SHA-256 of canonical `[issuer, subject, trusted tenant]`. Changing the issuer (switching IdPs, for example) or `auth.tenantClaim` gives every shopper a new actor ID, so their earlier conversations and memory records are no longer found. The same implementation is used in API and Runtime; clients cannot supply actor IDs. Raw events expire after `eventExpiryDays` (90 days by default, 7 to 365), which is distinct from derived-memory retention. See the privacy/deletion procedure in [operations](operations.md#memory-and-pii).

`longTerm=true` adds long-term memory: it runs the extraction strategies under `[memory.strategies]` and retrieves their derived records into each turn. It is off by default and in both templates, because it keeps derived data about each shopper; turn it on in Prod only after a privacy review. Each strategy is independent and maps to one retrieval namespace: `semantic` (facts), `summarization` (per-session summaries), `userPreference` (cross-session preferences). With `longTerm=true` at least one strategy must be enabled. Turning `longTerm` or a strategy on or off updates the Memory resource in place and keeps the conversation history: only the resource's name and encryption key are create-only. Turning one off removes it from the synthesized extraction/retrieval configuration; the code does not delete its existing derived records, and raw event expiry is not a derived-record deletion guarantee. Follow the deletion verification procedure in [operations](operations.md#memory-and-pii).

Short-term memory has no switch.

```toml
[memory]
longTerm = true
[memory.strategies]
semantic = true
summarization = false
userPreference = true
```

## Guardrail

A Bedrock Guardrail is applied to the agent's model calls by default. Its content filters cover sexual content, violence, hate, insults, misconduct and prompt attacks. Its sensitive-information policy blocks card numbers, CVVs and expiry dates, PINs, passwords, AWS access and secret keys, and Korean resident registration numbers written `YYMMDD-NNNNNNN`, on input and output (`packages/infra/components/guardrail.py`). `[guardrail] mode="ENFORCE"` blocks matched content; `mode="AUDIT"` keeps the filters enabled so matches surface in the trace but nothing is blocked — use it to measure impact before enforcing. `enabled=false` removes the guardrail entirely. Prod rejects `enabled=false` and requires `mode="ENFORCE"`. `streamProcessing` controls how replies stream through it. `"sync"` (the default, and the Prod template's) checks each reply before any of it reaches the shopper. On Dev the first text took 1.56 s with `async` and 0.76 s without a guardrail; with the guardrail checking the whole conversation, Converse's default, `sync` took 2.61 s against 1.71 s for `async` ([streaming and latency](../design-docs/streaming-and-latency.md#the-guardrails-stream-mode)). `"async"` (the Dev template) streams at once and checks in the background, so a reply that breaks a filter can show briefly before the stream is cut, and sensitive-information masking is unsupported ([Bedrock streaming guardrails](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-streaming.html)). The shipped filters block rather than mask.

On each model call the guardrail checks the shopper's latest message and the reply as it's generated, not the whole conversation again. A blocked message, the shopper's or Aria's, is replaced in the conversation with a short note, so one blocked turn doesn't block the turns after it.

`screenToolResults` (on by default) also runs the guardrail over each Gateway tool's result, as input, before the model reads it: a Converse guardrail doesn't evaluate tool results, and a target's data could carry a planted instruction. A result the guardrail blocks, or can't check (an image or a document, or a failed call), reaches the model as an error, logged as `tool_result_blocked` or `tool_result_screen_failed`. Local search results aren't screened. The sensitive-information filters apply to tool results too, so check a target's real data in `AUDIT` before enforcing: date-like fields can resemble card expiry dates, and short codes can resemble PINs.

The system prompt adds behavioral instructions; tool allowlists, size/budget checks, backend authorization and the guardrail provide separate controls.

## Custom headers

`gateway.forwardHeaders` configures Runtime header visibility and agent/interceptor `FORWARD_HEADERS`. It accepts non-credential context names: never `Authorization`/`auth`, no `x-aria-*` name in any casing, because that namespace is the agent's own and a forwarded header carries whatever value the caller sent, and not `x-correlation-id`, which the interceptor sets from the calling trace. Each target takes at most 10 headers, the actor and correlation headers included, so `forwardHeaders` names at most 8. The loader rejects these names, and the agent refuses to start if `FORWARD_HEADERS` holds one. Local tools receive current values in `invocation_state`; remote MCP custom headers are fixed when the session is built. The agent's Gateway token is read on each request.

The shipped browser/API does not forward custom headers to Runtime. Use a direct Runtime caller or implement and test that API path explicitly. Headers cannot expand the verified caller's data scope. See [identity and tokens](../design-docs/identity-and-tokens.md#custom-headers).
