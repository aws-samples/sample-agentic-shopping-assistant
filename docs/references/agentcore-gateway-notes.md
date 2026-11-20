# AgentCore Gateway: verified behavior

What ARIA relies on about AgentCore Gateway, including behavior the developer guide doesn't state. "Verified" means observed on a deployed stage; "documented" means stated in the AgentCore developer guide.

## Inbound authorization

- **Verified:** with a `CUSTOM_JWT` authorizer, a call without a token gets HTTP 401.
- **Verified:** a validly signed token that fails the audience or scope check gets HTTP 403. A shopper's own Cognito access token has no `aud`, so the authorizer's `allowedAudience` rejects it.
- **Verified:** `tools/list` includes a built-in `x_amz_bedrock_agentcore_search` tool. The agent's allowlist filters it out.

## REQUEST interceptor

- **Documented:** input arrives as `mcp.gatewayRequest.{headers, body}` when `passRequestHeaders` is on. Output must carry `interceptorOutputVersion: "1.0"` and `mcp.transformedGatewayRequest`.
- **Verified:** raising an exception denies the request. The caller sees HTTP 500 with "An internal error occurred. Please retry later.", and only the interceptor's log has the reason.
- **Verified:** the live input carried no `requestContext.requestId`, so a correlation ID can't be derived from it.
- **Verified:** with `passRequestHeaders`, the input's headers include `x-amzn-trace-id` on every request, and `traceparent` and `baggage` on the agent's calls, from its OpenTelemetry instrumentation.
- **Verified:** an output with `mcp.transformedGatewayResponse` is returned to the caller as given, here HTTP 200 with `application/json` and a JSON-RPC error body, and the target isn't called.
- **Documented:** headers the interceptor returns take precedence over the caller's, and must still be in the target's `allowedRequestHeaders`. An `Authorization` header from the interceptor replaces the target's own credential.

## Header propagation

Documented rules ([header propagation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-headers.html)):

- At most 10 request headers per target.
- Names must match `^[a-zA-Z0-9_-]+$`, and values must be printable ASCII up to 4 KB.
- Some names can't be propagated: `Authorization`, `Cookie`, `Content-Type`, `X-Forwarded-*`, `X-Amzn-*` and others.
- A header that the target's API-key configuration or its REST API schema provides can't be propagated. Declaring `x-aria-actor-id` as an OpenAPI parameter would also make it a model-filled argument, so ARIA's loader rejects such documents.

Verified:

- Lambda targets receive propagated headers in `context.client_context.custom["bedrockAgentCorePropagatedHeaders"]`.
- API Gateway, OpenAPI (inline and S3) and MCP targets receive them as HTTP request headers.

## Outbound credentials

For an OAuth or API-key target, the Gateway's service role makes three calls. Verified through CloudTrail:

| Call | Resources it is authorized against |
| --- | --- |
| `bedrock-agentcore:GetWorkloadAccessToken` | `workload-identity-directory/default` and the Gateway's workload identity, `workload-identity-directory/default/workload-identity/<gateway-name>-<suffix>` |
| `bedrock-agentcore:GetResourceOauth2Token` or `GetResourceApiKey` | The provider, its vault (`token-vault/default`) **and** the Gateway's workload identity |
| `secretsmanager:GetSecretValue` | The provider's secret, `bedrock-agentcore-identity!default/<oauth2\|apikey>/<provider>-<suffix>` |

The sample policy in the AgentCore guide grants the credential call on the provider only. That is denied ("not authorized to perform: bedrock-agentcore:GetResourceOauth2Token on resource: …/workload-identity/…"), and the target call fails with the generic internal error. CloudTrail, with event source `bedrock-agentcore.amazonaws.com`, names the denied resource. It's the fastest way to debug a target that returns "An internal error occurred".

## Target creation

- **Verified:** an MCP server target lists the server's tools when it is created, so the server and its credential must work at deploy time.
- **Verified:** for an API Gateway target, AgentCore calls `GetExport` with the deployer's credentials and parses the whole export. One method without a method response fails the target ("responses is missing"), even if that method isn't exposed. Lambda proxy methods export none unless you declare one.
- **Documented:** API Gateway targets exclude methods that combine `AWS_IAM` with an API key, and don't support `{proxy+}` resources or private endpoints. The API must be in the Gateway's account and Region.
- **Documented:** OpenAPI targets need an `operationId` per exposed operation, don't support `oneOf`/`anyOf`/`allOf` or parameter serializers, and refuse server URLs that resolve to private IP ranges.

## Policy engine (Cedar)

- **Verified:** in `ENFORCE`, a call the policies forbid gets a JSON-RPC error `-32002` over HTTP 200, "Tool Execution Denied: Tool call not allowed due to policy enforcement", naming the policy that denied it. The target isn't called.
- **Verified:** in `ENFORCE`, `tools/list` lists only the tools a policy permits. The Gateway's built-in search tool, which no sample policy permits, drops out of the list.

## Errors a tool call can return

- **Verified:** a credential failure or a target failure reaches the agent as a tool result with `isError: true` and "An internal error occurred. Please retry later.", over HTTP 200.
- **Verified:** an interceptor that raises denies the call, and a direct caller gets HTTP 500. ARIA's interceptor raises only for a message with no id to answer or an unexpected failure; a refused request gets its own JSON-RPC error (`-32003`).
