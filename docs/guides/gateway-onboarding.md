# Onboarding APIs and MCP servers to the Gateway

[한국어](gateway-onboarding.ko.md)

Put an API or an MCP server behind AgentCore Gateway, and the agent can call it as tools. You describe it under `[[gateway.targets]]`. CDK then creates the Gateway target, the permissions for its outbound credentials, and its Cedar policies.

A tool belongs here when it needs the shopper's identity or credentials of its own. Frequent tools that need neither work better as local tools ([tool placement](../design-docs/tool-placement.md)).

Your API can rely on three things:

- **Calls come from the Gateway,** authenticated with the credentials you configure for the target: an OAuth client, an API key, or the Gateway's IAM role.
- **Every tool call names the shopper** in the `x-aria-actor-id` request header. It is a 64-character hex actor ID that the agent sets and the Gateway's interceptor checks. Scope your data to it, and refuse calls without it.
- **Your API never receives the shopper's sign-in token,** or any token from the browser. See [identity and tokens](../design-docs/identity-and-tokens.md).

## Choose a target kind

| `kind` | For | Tools come from | Outbound credentials |
| --- | --- | --- | --- |
| `openapi` | An HTTPS API described by an OpenAPI 3.0 or 3.1 document | Each `operationId` | OAuth or API key |
| `apigateway` | A REST API stage in Amazon API Gateway | The `operations` you list | The Gateway's IAM role (SigV4) or an API key |
| `lambda` | A Lambda function | An inline `toolSchema` | The Gateway's IAM role |
| `mcp` | A remote MCP server over HTTPS | The server's own tool list | OAuth, or an API key sent as a bearer token |

The model sees each tool as `<target>___<tool>`. Only the tools in the target's `allowedTools` are exposed: if the server starts advertising a new tool, exposing it takes a configuration change.

## Steps

1. [Create the outbound credential](#1-create-the-outbound-credential), unless the target uses the Gateway's IAM role.
2. [Add the target](#2-add-the-target) to your configuration.
3. [Give it a Cedar policy](#3-give-it-a-cedar-policy). Prod requires one.
4. [Plan, review and deploy](#4-plan-review-and-deploy).
5. [Verify on the stage](#5-verify-on-the-stage).
6. If the model needs guidance on when to use the tool, mention it in the agent prompt (`packages/agent/src/prompts/system.md`).

## 1. Create the outbound credential

OAuth and API-key credentials live in AgentCore Identity, in the Gateway's account and Region. Keep secrets in a file rather than on the command line, and delete the file afterwards.

For an OAuth client on your IdP, using the client-credentials grant:

```sh
aws bedrock-agentcore-control create-oauth2-credential-provider --region <region> \
  --name orders --credential-provider-vendor CustomOauth2 \
  --oauth2-provider-config-input file://orders-provider.json
```

```json
{"customOauth2ProviderConfig": {
  "oauthDiscovery": {"discoveryUrl": "https://idp.example.com/.well-known/openid-configuration"},
  "clientId": "<client id>",
  "clientSecret": "<client secret>"}}
```

For an API key:

```sh
aws bedrock-agentcore-control create-api-key-credential-provider --region <region> \
  --cli-input-json file://stores-key.json   # {"name": "stores", "apiKey": "<key>"}
```

Use `credentialProviderArn` from the output as the target's `providerArn`. CDK grants the Gateway's role what AgentCore needs to fetch that one credential, and nothing more:

- `GetWorkloadAccessToken` for the Gateway's own workload identity;
- `GetResourceOauth2Token` or `GetResourceApiKey` on that provider, its vault and the Gateway's workload identity, which AgentCore checks as well;
- `secretsmanager:GetSecretValue` on that provider's secret.

OAuth targets use `grantType = "CLIENT_CREDENTIALS"`, the default. The Gateway gets a token for your API's own client, and `x-aria-actor-id` identifies the shopper. AgentCore Gateway has two other grants, but neither is usable here yet:

- `AUTHORIZATION_CODE` (three-legged OAuth) needs the shopper to consent in a browser, plus a return URL for that consent. ARIA sets no return URL and doesn't handle the consent flow, so the loader rejects this grant.
- `TOKEN_EXCHANGE` would make the Gateway exchange the agent's on-behalf-of token for one scoped to your API, so your API could verify the shopper cryptographically. ARIA's configuration doesn't expose it yet.

## 2. Add the target

These fields apply to every kind:

| Field | Meaning |
| --- | --- |
| `name` | Lowercase letters, digits and hyphens, starting with a letter; unique. Tools appear as `<name>___<tool>` |
| `kind` | `openapi`, `apigateway`, `lambda` or `mcp` |
| `allowedTools` | The tools the agent may call; at least one. Synth checks them against the document or schema where it can see it |
| `credentialType` | `GATEWAY_IAM_ROLE` (the default), `OAUTH` or `API_KEY` |
| `providerArn` | The credential provider from step 1 |
| `scopes` | The OAuth scopes the Gateway requests for your API |
| `grantType` | `CLIENT_CREDENTIALS`, the default and the only grant accepted; see step 1 |
| `apiKeyLocation`, `apiKeyName` | For `API_KEY` on `openapi` and `apigateway`: `HEADER` (the default) or `QUERY_PARAMETER`, and the header or parameter name, such as `x-api-key` |
| `policyFile` | A Cedar policy template, relative to `packages/infra` |

### OpenAPI

```toml
[[gateway.targets]]
name = "orders"
kind = "openapi"
schemaFile = "examples/orders-openapi.json"
allowedTools = ["getOrderStatus", "listRecentOrders"]
credentialType = "OAUTH"                  # or "API_KEY" with apiKeyName = "x-api-key"
providerArn = "arn:aws:bedrock-agentcore:<region>:<account>:token-vault/default/oauth2credentialprovider/orders"
scopes = ["orders/read"]
policyFile = "components/policies/orders.cedar"
```

- `schemaFile` is a JSON document under `packages/infra`, and synth checks `allowedTools` against its `operationId`s. Alternatively, `schemaS3Uri = "s3://bucket/key.json"` names a document in S3. The Gateway's role may read that one object, and synth can't check it.
- `servers` must be the API's fixed HTTPS URL. Avoid server variables that let a value choose the host. The Gateway refuses private IP addresses, so the API needs a public endpoint.
- The Gateway supports simple parameters and JSON bodies. It doesn't support `oneOf`, `anyOf`, `allOf` or custom parameter serializers ([AgentCore OpenAPI targets](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-schema-openapi.html)).
- **Don't declare `x-aria-actor-id`, `x-correlation-id`, or any `forwardHeaders` name, as a parameter.** The Gateway adds these headers itself and can't propagate a header the document declares. A declared parameter would also be an argument the model fills in. Synth rejects a `schemaFile` that declares one; your API reads the header from the request.
- For IAM-signed calls to API Gateway, use `kind = "apigateway"` instead.

### API Gateway

```toml
[[gateway.targets]]
name = "stores"
kind = "apigateway"
restApiId = "a1b2c3d4e5"
stage = "prod"
operations = [{ name = "find_store", path = "/stores", method = "GET", description = "Nearest stores" }]
allowedTools = ["find_store"]
credentialType = "GATEWAY_IAM_ROLE"       # or "API_KEY" with providerArn and apiKeyName
```

- `operations` fixes each tool's name, so the names, `allowedTools` and Cedar actions stay in step.
- With `GATEWAY_IAM_ROLE`, the Gateway signs its calls with SigV4, and its role may invoke only `<restApiId>/<stage>/*`. Set those methods' authorization to `AWS_IAM` so the API checks the signature. OAuth isn't supported for this kind.
- As with OpenAPI, don't declare `x-aria-actor-id` as a method request parameter.
- AgentCore reads the stage's whole OpenAPI export, not just the methods you expose. Give every method at least one method response, or target creation fails with "responses is missing". Lambda proxy methods export none unless you declare one.

### Lambda

```toml
[[gateway.targets]]
name = "loyalty"
kind = "lambda"
lambdaArn = "arn:aws:lambda:<region>:<account>:function:loyalty-points"
allowedTools = ["get_points"]
toolSchema = [{ name = "get_points", description = "The shopper's loyalty points balance", inputSchema = { type = "object", properties = { months = { type = "integer", description = "History to include, in months" } } } }]
policyFile = "components/policies/loyalty.cedar"
```

- **Invocation.** The Gateway invokes the function with its IAM role. A function in another account needs a resource policy that allows that role.
- **Schema.** Inline schemas support `type`, `description`, `properties`, `required` and `items`. Enforce any other constraint in the function.
- **Inputs.** The function gets the tool arguments as its event. The tool name is in `context.client_context.custom["bedrockAgentCoreToolName"]`, and the headers are in `["bedrockAgentCorePropagatedHeaders"]`.
- **Results.** Return the result object directly; the Gateway wraps it. Use `product.v1` for product cards. Raise an exception for a failed call: an API Gateway-style `statusCode`/`body` dictionary becomes ordinary tool content, even when it describes an error.
- **Example.** `packages/mcp-servers/saved-items-lambda` is a complete example, and [the Lambda target contract](#lambda-target-contract) below documents it.

### MCP server

```toml
[[gateway.targets]]
name = "catalog"
kind = "mcp"
endpoint = "https://catalog.example.com/mcp"
allowedTools = ["search_catalog"]
credentialType = "OAUTH"                  # or "API_KEY", sent as "Authorization: Bearer <key>"
providerArn = "arn:aws:bedrock-agentcore:<region>:<account>:token-vault/default/oauth2credentialprovider/catalog"
scopes = ["catalog:read"]
policyFile = "components/policies/catalog.cedar"
```

- The server must be reachable from the Gateway over HTTPS.
- `allowedTools` uses the names from the server's `tools/list`.
- `x-aria-actor-id`, `x-correlation-id` and any `forwardHeaders` arrive as HTTP headers on each MCP request.

To serve search from your own catalog server instead of the local tool, set `[tools.productSearch] provider = "disabled"` and onboard the server as a target. Every search then pays for the Gateway hop.

## 3. Give it a Cedar policy

`policyFile` is a Cedar template: a `.cedar` file under `packages/infra/components/policies/`, in any folder except `sample/`, which holds the saved-items sample's own policies. The loader rejects a path outside that folder or a file that doesn't exist. `{{GATEWAY_ARN}}` and `{{TOOL}}` are filled in once for each allowed tool, where `{{TOOL}}` is `<target>___<tool>`. Cedar denies by default, so a tool without a permit is refused once the policy engine enforces. Start from the sample's `components/policies/sample/01-allow-shopper-tools.cedar`; trimmed, it reads (the file also bounds `limit` to 1–20):

```cedar
permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"{{TOOL}}",
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when { context has input };
```

- **`policy.mode = "ENFORCE"`** needs a `policyFile` for every customer target, in every stage. A managed Gateway in Prod requires Cedar on in `ENFORCE`.
- **Before enforcing,** test allows and denials in Dev with `policy.mode = "LOG_ONLY"`.
- **The principal** is the shopper with an on-behalf-of token. Policies can match tool arguments in `context.input`; see [identity and tokens](../design-docs/identity-and-tokens.md#cedar-and-data-authorization).
- **Cedar authorizes the call, not the data.** Your API must still scope its data to the actor ID.

## 4. Plan, review and deploy

`pnpm check` synthesizes the stacks and rejects invalid targets, such as:

- an unknown `operationId`;
- a missing `providerArn` or `apiKeyName`;
- a document that declares a propagated header;
- more than 10 propagated headers.

Then deploy through the reviewed plan in [release](release.md). In the diff, check the new target, its IAM grants, and that nothing becomes public.

## 5. Verify on the stage

- Ask the agent something that needs the tool. Check the tool call and its result in the trace or in the web app.
- Your API's logs should show the Gateway's credentials and `x-aria-actor-id`, and no token from the browser.
- A `tools/call` without `x-aria-actor-id` must fail. The interceptor denies it, and your API should refuse it too.
- If the model isn't offered the tool, compare `allowedTools` with the Gateway's `tools/list`.

## Use an existing Gateway

Set `gateway.mode = "external"` to call a Gateway you manage elsewhere:

```toml
[gateway]
mode = "external"
endpoint = "https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp"
allowedTools = ["orders___getOrderStatus"]

[gateway.agentAuth]
flow = "on_behalf_of"
credentialProvider = "shopping-gateway"
scopes = ["shopping:tools"]
```

- `endpoint` must be an AgentCore Gateway URL, `https://<id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp`. The agent sends its token to no other host, and synth fails on anything else.
- `allowedTools` lists full tool names, as that Gateway exposes them.
- Its authorizer must accept the token `credentialProvider` mints for the agent; see [identity and tokens](../design-docs/identity-and-tokens.md#the-agents-gateway-token).
- ARIA creates no targets, interceptor or policies in this mode. The Gateway's owner provides them, including the `x-aria-actor-id` check.
- Prod requires `authorizationVerified = true`, once you have tested denials.

## Run without a Gateway

`gateway.mode = "disabled"` creates no Gateway, and the agent keeps its local tools, such as product search. This mode requires `policy.enabled = false` and no `gateway.targets`. The Runtime gets `GATEWAY_ENABLED=false` and skips the endpoint lookup.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Synth fails | The message names the field: an unknown `operationId`, a missing `providerArn` or `apiKeyName`, a declared propagated header, or too many headers |
| The model isn't offered the tool | `allowedTools` against the Gateway's `tools/list`; the agent filters on these names |
| The agent gets 401 or 403 from the Gateway | The agent's token; see the troubleshooting table in [operations](operations.md) |
| Tool calls fail with "Access denied by the Gateway's access check" | The interceptor refused the call. Its log has the reason, and the `InterceptorDenials` alarm fires |
| Tool calls fail with "An internal error occurred" | Your target failed, its credential couldn't be fetched, or the interceptor itself failed. The target's and the interceptor's logs have the reason |
| Your API returns 401 or 403 | The credential provider, its scopes, or `apiKeyName` and `apiKeyLocation` |

## What's verified

The [quality score](../QUALITY_SCORE.md#gateway-and-identity) records which target kinds, credentials and Cedar modes have run end to end through the agent.

## Lambda target contract

### Target invocation contract

Gateway passes tool arguments directly in `event`:

```json
{"limit":5}
```

The tool name and the propagated headers are context metadata, not event fields:

```python
context.client_context.custom.bedrockAgentCoreToolName
# "saved-items___get_saved_items"
context.client_context.custom.bedrockAgentCorePropagatedHeaders
# {"x-aria-actor-id": "<64 hex characters>", "x-correlation-id": "<32 hex characters>"}, plus any gateway.forwardHeaders
```

`_tool_name()` strips the `___` prefix. The handler requires the resulting name to equal `get_saved_items`; missing and unknown names raise `ValueError("Unknown saved-items tool")`. It accepts only the `limit` argument.

The shopper's identity comes from `x-aria-actor-id`, which the agent sets in code (never the model) and the interceptor checks before the Gateway calls the target. The handler matches the header name case-insensitively and requires a 64-character lowercase hex actor ID. Identity must never be a model-writable tool parameter, and a target must never accept one as an argument.

### Result and error contract

Return the result object directly:

```python
return {"schemaVersion": "product.v1", "count": len(items), "products": items}
```

Gateway creates the MCP content envelope. Do not return an API Gateway `statusCode`/`body` wrapper or build a nested MCP envelope: those become ordinary tool content. `products` is a list of catalog objects; `count` is its length. The web client requires `schemaVersion == "product.v1"` and validates the product fields before rendering cards.

| Condition | Lambda behavior |
| --- | --- |
| Valid call | Return the direct `product.v1` object: a stable sample of the example catalog for that actor, up to `limit` (5 by default, at most 20) |
| Missing or malformed actor ID | Raise `PermissionError("Saved items need the shopper's identity")` |
| Unknown/missing tool | Raise `ValueError` |
| Unsupported arguments | Raise `ValueError` |

These are Lambda tool failures, not HTTP 400/500 response dictionaries. The web hides failed tool calls from the transcript; the agent either corrects the call or explains the failure.

The sample's example catalog data stands in for your store: a real target reads the shopper's own records, scoped by the actor ID.
