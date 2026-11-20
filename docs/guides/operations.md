# Operations and privacy

[한국어](operations.ko.md)

Assign an on-call owner, a tested alarm destination and customer SLO/RPO/RTO before production. All actions below that change service state, data, account settings or spend require the operator's normal change authorization. This guide does not execute them.

## Signals

`{namespace+stage}-operations` shows API/agent errors, p95 turn duration, model input/output tokens, product search failures and alarm status. The Operations stack alarms on API/agent errors, failed session builds, model throttling, tool denials, failed product searches and, with OpenSearch, failed query embeddings, unhealthy targets, too few healthy API tasks, target and load-balancer 5xx, WAF rate-limit blocks, interceptor refusals, Gateway Lambda (interceptor, and the saved-items sample when deployed) errors or throttles, and OpenSearch red state, low disk and failed automated snapshots. [RELIABILITY.md](../RELIABILITY.md#alarms) lists each alarm, its threshold and its periods; only `ApiHealthyHosts` treats missing data as breaching. Configure independent synthetic monitoring if the customer needs an availability SLO.

The Runtime's structured events to search first: `agent_turn_failed` (with `code`), `session_build_failed`, `tool_denied` (with `reason`), `tool_budget_ended_turn`, `agent_turn_superseded`, `interrupted_turn_closed`, `tool_result_blocked`, `tool_result_screen_failed`, `gateway_http_error`, `gateway_request_failed`, `gateway_token_failed`, `local_tool_not_allowed`, `product_search_failed`, `product_search_embedding_failed` (searches running by keyword) and `product_search_invalid_product` (catalog products that don't make a card). The interceptor logs each denial as `interceptor: target invocation denied: <reason>`.

API and Runtime emit structured JSON. The API exports OTLP traces to its ECS collector; the Runtime starts through `opentelemetry-instrument` and uses managed telemetry routing. Verify actual service names and trace/log destinations after deployment. A running application does not prove telemetry delivery; collector failure can leave the API healthy without traces.

Runtime logs are retained 30 days, WAF logs 30 days, and ALB access archives use a 90-day lifecycle. API, Lambda and OpenSearch log retention is declared with the resource. WAF log headers redact Authorization/Cookie and sampled requests are disabled. Runtime content capture is off by default; do not put tokens, prompts, catalog secrets or Memory payloads in incident tickets. Structured exception traces may contain downstream error details and need restricted access.

Online Evaluations require explicitly approved `operations.captureContent=true`, verified `transactionSearchReady=true`, the actual `traceLogGroup`, and `evaluations.enabled=true`. A new unified Runtime generally uses its own Runtime log group; `aws/spans` is the legacy split destination. Read the current destination and inspect a known trace before configuring the evaluator. Account-level Transaction Search activation is not performed by this CDK app. Ensure the selected built-ins/judge are available in the account/region, measure actual scored-session coverage, and never treat zero scored sessions as good quality. Step-by-step setup, verification and troubleshooting: [evaluations](evaluations.md).

## Incident triage

| Symptom | Read first | Response |
| --- | --- | --- |
| Login/API 401/403 | IdP issuer/JWKS, access-token `aud` or `client_id`, scope, expiry; API/Runtime/Gateway authorizer settings | Correct configuration/key rotation or sign in again. Do not bypass JWT verification or substitute SigV4 for a CUSTOM_JWT Runtime. |
| Model 403/429 | Configured profile/model access, role policy, account restrictions and quotas | Resolve access or approved quota; consider a reviewed compatible model/configuration rollback. There is no automatic model fallback or cross-provider data transfer. |
| Model 500 on every turn | `agent_turn_failed` with `code` `internal` and `error_type` `InternalServerException`; one tiny `bedrock-runtime converse` call to the same model from the stage's Region | A Bedrock-side incident with the model: every turn fails until it recovers, with no fallback ([tracker](../exec-plans/tech-debt-tracker.md)). Wait it out, or switch `runtime.modelId` to a model that answers through a reviewed deploy. |
| Agent/Gateway initialization failures | Gateway/target readiness, credential provider, discovery, Cedar decisions, Runtime IAM dependencies | Repair the dependency and retry a new request. Failed session builds are not cached. Do not switch Prod to LOG_ONLY to restore availability. |
| Slow/failed turns | End-to-end traces, API latency, model throttles, Lambda duration/throttles, search health | Identify the slow dependency before raising deadlines. Default boundaries: model 60-second read, HTTP adapter 10 seconds, interceptor and sample Gateway Lambda 10 seconds, MCP read 30 seconds, turn 120 seconds, browser request 130 seconds, ALB idle 150 seconds. |
| ECS unhealthy targets | Task events, `/ping`, container port/configuration, startup logs, collector/resource pressure | Inspect the new task revision and rollback a compatible application release if necessary. Health probes do not validate model/IdP/search access. |
| OpenSearch red/disk/snapshot alarm | Cluster/node health, storage, JVM pressure, shard allocation, replica and snapshot status | Stop index promotion. Recover capacity/replicas under an approved change; restore into an isolated domain before switching traffic. Never delete the active index to recover space without a recovery plan. |
| Missing logs/evaluations | IAM, Runtime log-group ownership, collector logs, trace destination, `service.name`, sampling and capture settings | Restore delivery/coverage. Do not enable broad content logging as the first diagnostic step. |
| Suspected token/tool abuse | WAF/ALB/API metadata, IdP sessions, `tool_denied` and interceptor denials, Gateway decisions and affected target | Revoke identity access and restrict the affected integration through the customer incident process. Preserve limited evidence; use backend authorization as the final data boundary. |
| `WafRateLimitBlocks` | Blocked client IPs in the WAF log group | Shoppers behind one NAT (an office, a campus) share an IP. If the blocked IPs are legitimate, raise `api.wafRateLimit` through a reviewed deployment. |

The web Stop button cancels the browser stream and releases its reader. It does not promise immediate cancellation of a model/tool action already executing. Conversation deletion explicitly calls `StopRuntimeSession` before deleting available Memory records. Runtime manages idle expiry (900 seconds) and maximum lifetime (3600 seconds); there is no application session database or per-session background reaper.

## Capacity and cost

The Dev template uses one NAT gateway, one API task, the synthetic example catalog in memory, and Memory without long-term extraction. OpenSearch, the catalog bucket and the indexing task are created only for the `opensearch` provider. These choices reduce idle cost but do not establish high availability.

The managed Prod VPC uses three NAT gateways; the API starts with at least two tasks and scales on CPU/memory and ALB requests. The example Prod OpenSearch deployment uses three data nodes plus three masters across AZs. Its redundancy has a substantial fixed cost even with no conversations. Validate shard/replica counts, storage headroom and throughput against the real catalog. Each index has one primary shard and an explicit replica count. A 1024-dimension vector takes 4 KB per product, before the HNSW graph, and every version kept on the domain takes its own space. The indexing task runs only while a load runs, on 1 vCPU and 4 GiB.

The interceptor reserves 20 concurrent executions and the saved-items sample 10. Every Gateway tool call passes through the interceptor, so 20 caps a stage's concurrent Gateway calls. Validate this against service quotas, API scaling and tool fan-out. Raising task count alone does not raise Lambda, Gateway, AgentCore or model capacity. The agent limits tools per turn and turns per Runtime container; these are abuse boundaries, not durable per-user monetary quotas.

Measure active/idle Runtime sessions, model input/output tokens, cross-region inference, Embed v4 calls (one per 96 products indexed, and one per search with a query), Memory events and extraction/retrieval, log/trace volume and evaluation judge usage. Customer-managed budgets and cost anomaly notifications should cover the whole account plus `namespace`/`stage` tags. None are silently installed by this project. Tune availability/cost tradeoffs explicitly; do not scale Prod to a single AZ to meet an unreviewed cost target.

## Memory and PII

Every deployment keeps each conversation in AgentCore Memory as raw events (short-term memory). They expire after `memory.eventExpiryDays`, 90 days by default (7 to 365). Long-term memory (`memory.longTerm`) is off by default; with it on, extraction derives preferences, facts and summaries about each shopper, so turn it on in Prod only after a privacy review. Derived preferences/facts are separate records: raw expiry does not prove those records were erased. The hashed actor ID remains linked to a person; treat it as personal data.

The system prompt avoids soliciting payment credentials, passwords, precise addresses and sensitive traits. This is a behavioral control, not a DLP guarantee. Customer consent, data classification, allowed content, encryption-key policy, residency and retention requirements govern the event retention period and whether long-term memory or content capture may be enabled. Global model inference is a separate residency decision.

Deletion semantics:

- `DELETE /v1/sessions/{session_id}` stops the owned Runtime session, removes paginated raw events and its summary, and retains cross-session saved preferences/facts.
- `DELETE /v1/memory` stops sessions discoverable in that actor's Memory history, removes their records, then removes preferences/facts. It returns a completion message and `verificationRequired=true`; the response does not include deletion counts.
- An upstream failure returns an error; the API does not claim a complete erasure. Asynchronous extraction may recreate derived records from in-flight work, and new/concurrent sessions can write again.
- Memory keeps listing a session after its events are deleted, so `GET /v1/sessions` skips any session with no event left. Verify an erasure by the actor's events and records, never by Memory's own session list.

For a privacy erasure request, first stop new processing through the trusted IdP/application access controls. Inventory the actor's sessions, complete the explicit deletion, and wait for in-flight extraction to settle. Re-list raw and derived records with the correctly scoped namespace, repeat approved deletion if necessary, and record completion evidence without copying content. Also address trace/log archives, exports, backups and any customer backend under their retention policies. Retain the user's consent/revocation decision in the customer privacy process. This stateless sample does not provide a durable erasure/revocation ledger.

When a stack or resource is removed, Prod keeps Memory, the OpenSearch domain, the catalog bucket, the Cognito user pool, and the API, Gateway Lambda, indexing-task and VPC flow-log groups (`RemovalPolicy.RETAIN`); Dev deletes them, emptying the catalog bucket first. The Runtime, WAF and OpenSearch log groups, the evaluation results log group, the load balancer's access-log bucket and the domain's Nori association are kept in every stage; a deleted Dev domain takes its plugin with it. A kept Memory keeps its name, `<ns>_memory` in lowercase (`ariadev_memory` for the Dev template), so re-creating the AgentRuntime stack in the same namespace collides with it until the kept Memory is deleted deliberately; this hasn't been run on a live stage. Keeping a resource is not a recovery plan.

## Catalog runbook

The example catalog is synthetic; real catalog data is customer-owned and never committed. Establish source ownership/license, refresh cadence, validation, access and recovery responsibility before using customer data. Writing a catalog definition is covered in [integrations](integrations.md#bring-your-own-catalog), and the design in [catalog search](../design-docs/catalog-search.md).

With the `opensearch` provider, a stage's catalog lives in versioned indexes, `<alias>-<version>`, behind one alias, `tools.productSearch.openSearchIndex` (default `products`). The indexing job, a Fargate task in the VPC, builds each version. The `fixture` and `http` providers need no indexing. A deploy creates the domain, the catalog bucket and the task, but no index: until the first load is promoted, every search fails (`product_search_failed`), so index the catalog right after a stage's first deploy.

OpenSearch automated snapshots are not a tested disaster-recovery process. Define cross-account/region recovery needs separately, validate snapshot success, and rehearse restoration to an isolated destination. Record measured RPO/RTO before production.

### Index the catalog

From the repository root, with the stage's reviewed configuration:

```sh
ARIA_CONFIG=/absolute/path/to/reviewed.toml sh scripts/index_catalog.sh --catalog /absolute/path/catalog.jsonl --version v2
ARIA_CONFIG=/absolute/path/to/reviewed.toml sh scripts/index_catalog.sh --catalog /absolute/path/catalog.jsonl --version v2 --apply
```

| Argument | Meaning |
| --- | --- |
| `--catalog` | A local JSON array or JSONL file, or `s3://<catalog bucket>/catalog/<key>`: the task can read only that prefix |
| `--version` | The new index is `<alias>-<version>`: lowercase letters, digits and hyphens, at most 63 characters, starting with a letter or digit. It must not already exist |
| `--replicas` | Replicas per shard, 0 to 2. By default the stack's `IndexReplicas` output: 0 on single-node Dev, 1 in Prod. The script reads it at `--apply`, so a dry run's manifest reports `null` unless you pass one |
| `--apply` | Write. Without it, nothing is written |

- **Dry run, the default:** a local file is validated on your machine, with the stage's catalog definition from the same loader the deploy uses, and no AWS call. An `s3://` catalog is validated by the task, which then writes nothing. The manifest gives the products read, accepted and rejected, the reasons by field (`rejectedReasons`), the source's SHA-256, the index and pipeline names, and the embedding calls, characters and vector bytes the load would add. Fix rejections before you apply: rejected products are left out of the index.
- **`--apply`:** the script checks that the Nori plugin is `ACTIVE` on the domain, when the definition uses `nori`. Associating it finishes after the deploy, as a blue/green deployment on the domain, so wait and retry while it's `ASSOCIATING`. It then uploads a local file to `s3://<bucket>/catalog/<version>/<file>`, runs the task (`aws ecs run-task`) in the stage's ECS cluster, private subnets and the task's own security group, and waits until the task stops. The task validates the catalog again, creates the index, embeds and loads the products 96 at a time, checks the count, puts the search pipeline `<alias>-hybrid` and moves the alias.
- **The result:** the script prints the task's manifest, one JSON line, on stdout, and exits with the task's exit code. A promoted load has `"applied": true`, `"promoted": true` and `previousIndices`, the indexes the alias left. A failed one has `error`, which names the step and the error type, never product content, and `created`, `loaded` and `indexed` show how far it got. The task's log is in the stack's `IndexerLogGroup`, stream `indexer/indexer/<task ID>`.
- **Before you start:** the script needs the AWS CLI, `uv` and `python3`, and stops unless the provider is `opensearch` and the deployed stack's alias matches the configuration's. Its credentials read the `Opensearch` stack's outputs (`cloudformation:DescribeStacks`) and the Nori status (`es:ListPackagesForDomain`), write the bucket's `catalog/` prefix (`s3:PutObject`), run and watch the task (`ecs:RunTask`, `ecs:DescribeTasks`, and `iam:PassRole` for its task and execution roles), and read its log (`logs:GetLogEvents`). The task's own role does the indexing.

After a promoted load, run representative searches: a filter-only search, a free-text query, and a Korean query when the definition uses Nori.

### Promote, roll back and clean up

- **Promotion:** the last step moves the alias from every index behind it to the new one in one `_aliases` call, so searches switch at once.
- **Roll back** by indexing the earlier source again as a new version. Or move the alias back to the previous index, which the manifest names in `previousIndices`: from an approved environment inside the VPC with index-management access, such as an instance in a private subnet with its own TCP 443 rule into the domain's security group, send one signed request:

  ```http
  POST /_aliases
  {"actions": [{"remove": {"index": "products-v3", "alias": "products"}}, {"add": {"index": "products-v2", "alias": "products"}}]}
  ```

- **After a failure:** a failure before promotion leaves the alias where it was, so shoppers keep the current version. The index the task created stays, unpromoted, with whatever it loaded. The task can't delete indexes: delete the unpromoted index from the same kind of approved environment (`DELETE /<alias>-<version>`) under a change, then index again, with a new version or the same one once its index is gone.
- **Old versions** stay: neither the task nor the agent deletes an index. Keep at least the previous good version for rollback, and delete older ones under the storage/retention policy; `SearchDiskLow` alarms below 10 GiB free.
- **A concrete index named like the alias:** the task stops before writing anything. Set `tools.productSearch.openSearchIndex` to another alias, such as `catalog-products`, deploy, then index under it. The agent's search tool and the task's role follow that setting.

### Change the catalog definition

The deploy gives the Runtime and the indexing task the new definition together, but an index keeps the mapping and the products it was built with.

- **Without a new index:** descriptions, parameter names, boosts, `[catalog.search].k`, and removing a filter or one of its values change only the tool and the query. They reach shoppers with the deploy.
- **With a new index:** anything else, such as a field, a type, a value added to a filter (products with it were rejected), the card, the analyzer, or the embedding text or model, needs a new version. Deploy, then index again at once ([index the catalog](#index-the-catalog)). Until the new version is promoted, searches run against the old index: a filter on a field it lacks matches nothing, and a card field it lacks drops its products.
- **With `http`:** your API receives the new parameters, named as the definition names them, from the deploy on.
- **The analyzer:** `nori` adds the plugin to the domain, a blue/green deployment; plan that change on its own. Turning it off leaves the plugin associated, so the Nori-analyzed versions keep serving searches and rollbacks. To remove the plugin, delete those indexes, then dissociate it yourself (`aws opensearch dissociate-package`); the service refuses while an index still uses it.

## Rotate credentials

| Credential | Where it lives | Rotate | Then check |
| --- | --- | --- | --- |
| A Gateway target's OAuth client secret | An AgentCore Identity OAuth2 provider (the target's `providerArn`) | `aws bedrock-agentcore-control update-oauth2-credential-provider --name <provider> --credential-provider-vendor CustomOauth2 --oauth2-provider-config-input file://<new-config>.json`. The ARN stays the same, so nothing is redeployed | A call to one of the target's tools ([live checks](testing.md#live-checks-after-a-deploy)) |
| A Gateway target's API key | An AgentCore Identity API-key provider | `aws bedrock-agentcore-control update-api-key-credential-provider --cli-input-json file://<file>.json` with `{"name": "<provider>", "apiKey": "<new key>"}`. Same ARN, no redeploy | As above |
| The HTTP catalog credential | Secrets Manager (`tools.productSearch.secretArn`) | Store the new value as a new secret version. Running containers re-read it within 5 minutes, so keep the old key valid for that long | A search turn |
| The agent client's Cognito secret | Read from the agent client when the Auth stack deploys, and stored in the `<ns>-gateway` provider | Replacing it means a new agent client through a reviewed deploy; the interceptor and the Gateway authorizer use its client ID. Plan and test it on a stage first; this hasn't been exercised | The live checks |
| Your IdP's signing keys (BYO OIDC) | Your IdP's JWKS | Publish the new key before signing with it. The API caches keys for 5 minutes | A sign-in and a turn |

Keep secrets in files, never on the command line, and delete the files afterwards.

## Tool configuration and troubleshooting

The two Gateway Lambdas in `packages/mcp-servers` receive environment variables from `packages/infra/stacks/gateway_stack.py`. The agent's local product search reads its catalog settings from the AgentCore Runtime's environment, set by `packages/infra/stacks/agent_runtime_stack.py`. None of them read `.env` or start a server. The indexing task gets its settings from `packages/infra/components/catalog_indexer.py`, and its arguments from `scripts/index_catalog.sh` ([catalog runbook](#catalog-runbook)).

### Product-search environment (Runtime)

| Variable | CDK source / purpose |
| --- | --- |
| `SEARCH_PROVIDER` | `tools.productSearch.provider`: `fixture`, `opensearch`, or `http`; `disabled` removes the tool |
| `CATALOG_DEFINITION`, `CATALOG_DEFINITION_2`, … | The catalog definition (`catalogFile`, or the packaged example) as compact JSON, validated at synth, split across values of at most 2,048 bytes (at most 8) and joined in order. The tool's schema, argument checks, query and cards come from it; empty with `disabled` |
| `SEARCH_ENDPOINT` | `endpoint`: HTTPS catalog URL for the HTTP provider |
| `SEARCH_SECRET_ARN` | `secretArn`: optional Secrets Manager credential for the HTTP adapter |
| `SEARCH_RESULT_PATH` | `resultPath`: dotted response path to the product array, default `products` |
| `OPENSEARCH_HOST` | Managed domain endpoint, or empty when no domain is selected |
| `OPENSEARCH_PRODUCT_INDEX` | `openSearchIndex`, default `products`: the alias searched. Its search pipeline is `<alias>-hybrid` |
| `AWS_REGION` | Supplied by the Runtime. Outside it, set `AWS_REGION` or `AWS_DEFAULT_REGION`: the catalog's AWS clients, OpenSearch, Bedrock and Secrets Manager, fail without one |

The HTTP adapter accepts a secret JSON value with `header` (`Authorization` or `x-api-key`) and `value`. It caches the credential for 5 minutes and fetches it again once after a 401 or 403, refuses redirects, uses a 10-second timeout, and caps the response at 256 KiB and 20 products. Credentials and URLs are configuration, never model arguments. See [integration contracts](integrations.md#provider-contracts).

The indexing task gets `CATALOG_DEFINITION` and the values after it, the same as the Runtime, `OPENSEARCH_HOST`, `OPENSEARCH_PRODUCT_INDEX` and `AWS_REGION`.

The Runtime also receives the agent's Gateway token settings: `GATEWAY_AUTH_FLOW`, `GATEWAY_CREDENTIAL_PROVIDER`, `GATEWAY_TOKEN_SCOPES` and `GATEWAY_TOKEN_AUDIENCE`, derived from `gateway.agentAuth`. See [identity and tokens](../design-docs/identity-and-tokens.md#the-agents-gateway-token) for the token flow.

### Interceptor environment

| Variable | CDK source / purpose |
| --- | --- |
| `LOG_LEVEL` | `INFO` |
| `FORWARD_HEADERS` | Comma-separated `gateway.forwardHeaders` names |
| `OIDC_TENANT_CLAIM` | `auth.tenantClaim`: used to derive the actor ID from an on-behalf-of token, exactly as the agent does |

The interceptor requires a well-formed `x-aria-actor-id` on every tool call and, for an on-behalf-of token, requires it to match the token's subject. It never forwards `Authorization`; custom forwarding cannot carry `Authorization`, `auth` or a replacement `x-aria-actor-id`. Check failures and exceptions deny the request. The Gateway interceptor configuration sets `pass_request_headers=True`.

### Saved-items environment

| Variable | CDK source / purpose |
| --- | --- |
| `LOG_LEVEL` | `INFO` |

The sample needs no other configuration. It is created only when `gateway.savedItemsSample` is true.

### Packaging, network and permissions

Both Gateway Lambdas use Python 3.13, ARM64, `handler.lambda_handler` and active tracing, and neither is attached to the VPC. Each grants the Gateway execution role permission to invoke it.

- **Saved-items sample:** reserved concurrency 10, 10-second timeout, 256 MiB. The asset holds only `saved-items-lambda/src/` and `packages/catalog/src/aria_catalog`, and its hash covers only those folders, so unrelated package changes never redeploy it. Local bundling copies them without Docker.
- **Interceptor:** only stdlib code is packaged from `src/`, without a bundling step. Reserved concurrency 20, 10-second timeout, 256 MiB.
- **Runtime network:** the Runtime always runs in the VPC's private subnets, with its own security group. In the managed VPC it reaches AWS services through the interface endpoints listed in `vpc.interfaceEndpoints` (ECR and CloudWatch Logs by default; add `bedrock` and `ssm` for Bedrock and SSM) and S3 through the gateway endpoint. Everything else goes through the VPC's NAT gateway: the Gateway, AgentCore Identity and Memory, Secrets Manager, public HTTP catalogs, and any AWS service without an endpoint. An existing VPC (`vpc.vpcId`) needs the same routes.
- **Availability Zones:** AgentCore supports VPC connectivity only in some AZ IDs in each region, and creating a Runtime with a subnet elsewhere fails. Preflight checks the AZ IDs of the Runtime's subnets ([release](release.md#read-only-preflight)).
- **Network interfaces:** AgentCore keeps a Runtime's network interfaces for up to 8 hours after the Runtime is deleted. Until they're gone, CloudFormation can't delete the Runtime's security group or the subnets they're in, so a stack deletion fails (`DELETE_FAILED`); delete the stack again once the interfaces are released.
- **Product search, OpenSearch provider:** search runs in the agent, and the domain is reachable only from the VPC. An explicit TCP 443 rule allows the Runtime's security group into the domain's security group. The Runtime role may only `es:ESHttpPost`/`es:ESHttpGet` on the alias's `_search` paths, and `bedrock:InvokeModel` on the definition's embedding model for the query embedding. It reaches Bedrock through the NAT gateway unless `vpc.interfaceEndpoints` adds `bedrock`.
- **Indexing task:** Fargate, ARM64, 1 vCPU and 4 GiB, a read-only root file system, in the private subnets with no public IP. Its own security group has one TCP 443 rule into the domain's and no ingress. Its role reads only the catalog bucket's `catalog/` prefix, invokes only the embedding model, and may GET, HEAD, PUT and POST only the alias, `<alias>-*`, `_aliases` and the pipeline: it can't delete. It reaches ECR, CloudWatch Logs and S3 through the VPC's endpoints, and Bedrock through the NAT gateway unless `vpc.interfaceEndpoints` adds `bedrock`.
- **Catalog bucket:** private, with every public access block on, TLS only, S3-managed encryption and versioning, and no access-log bucket. Operators write it through the script, and only the indexing task's role reads it.
- **Product search, fixture/HTTP providers:** no domain is needed. An HTTP catalog must be reachable from the VPC, through the NAT gateway for a public one. A configured secret receives a resource-specific read grant on the Runtime role.
- **Gateway authorizer:** accepts only the agent's own token. With `on_behalf_of` it requires the tools audience and scope; with `m2m` it requires the agent's client ID and scope. A shopper's token is rejected.
- **Targets:** every target's `allowedRequestHeaders` lists `x-aria-actor-id` and `x-correlation-id` first, then the configured forward headers (at most 10 in all).

### Validation and deployment

Run offline checks from the repository root:

```sh
pnpm check
```

Use `ARIA_CONFIG` to select a reviewed TOML. Do not overwrite an active `.toml` with a template. From a clean, committed checkout, the separately authorized plan step performs read-only AWS inspection and local synthesis:

```sh
ARIA_CONFIG=/absolute/path/to/reviewed.toml sh scripts/plan_deployment.sh
```

Review the saved assembly, diff and configuration hash before authorizing `scripts/deploy.sh` with `ARIA_ALLOW_DEPLOY=yes`. Follow [release](release.md); do not substitute direct approval-bypassing CDK commands. Bootstrap and resource imports are separate operator work.

### Logs and troubleshooting

CDK creates `SavedItemsLogGroup` and `InterceptorLogGroup` with one-month retention; Dev deletes them with the stack and Prod keeps them (`RemovalPolicy.RETAIN`). Both handlers use standard `logging`, with `LOG_LEVEL=INFO`. The sample logs the actor ID prefix and result count; the interceptor logs the method and forwarded header names. Neither logs header values or tokens in those normal messages. Product search logs to the Runtime's log group: the result count and duration, the embedding fallback and dropped products by count, and failures with their traces, never argument values or products. Exception traces require restricted access.

There is no custom health endpoint. Inspect Lambda Invocations, Errors, Duration, Throttles, logs and authorized end-to-end tool calls.

| Symptom | Check |
| --- | --- |
| Empty successful search | Selected provider, actual fixture/index contents and filters. No criteria returns an empty list without a backend call. After a definition change, a filter on a field the index lacks matches nothing until the catalog is indexed again. |
| Search returns an invalid-value error | The model sent a value the definition doesn't declare, or a parameter the tool doesn't have. The error lists what's accepted so the model can correct the call; check the tool schema and the definition's descriptions if it keeps happening. |
| OpenSearch connection failure | The Runtime-to-domain 443 rule, `OPENSEARCH_HOST` and the exact `_search` IAM grants |
| Every search fails right after a deploy | With OpenSearch, the alias has no index until the first load is promoted: [index the catalog](#index-the-catalog) |
| `product_search_embedding_failed`, `SearchEmbeddingFailures` | Searches run by keyword. Its `error_type` names the failure: model access for the embedding model, the Runtime role's `bedrock:InvokeModel` grant, Bedrock throttling or the route to Bedrock (the NAT gateway or the `bedrock` endpoint) |
| The indexing task fails | The manifest's `error` and the task's log: the Nori association not `ACTIVE`, Bedrock access or throttling, rejected documents (error types only) or a count mismatch. The alias is unchanged; delete the unpromoted index ([after a failure](#promote-roll-back-and-clean-up)) |
| The agent cannot get a Gateway token | `GATEWAY_CREDENTIAL_PROVIDER` and the Runtime role's `GetResourceOauth2Token` grant. With managed Cognito on-behalf-of, the web app must request `aria-tools/invoke` (`OIDC_SCOPES`) and the user pool domain must use managed login. See [identity and tokens](../design-docs/identity-and-tokens.md#the-agents-gateway-token). |
| Gateway returns 401 or 403 | The minted token's flow, audience and scopes against the Gateway authorizer (`gateway.agentAuth`). A call without a token gets 401. A shopper's own token is always rejected, with 403. |
| Interceptor denies a tool call | Missing or mismatched `x-aria-actor-id`: the agent sends it with every Gateway request, so check `OIDC_TENANT_CLAIM` and the interceptor log |
| Target lacks the actor or a custom header | The target's `allowedRequestHeaders`, `pass_request_headers=True`, the Runtime allowlist and the interceptor allowlist; the API does not forward custom headers |
