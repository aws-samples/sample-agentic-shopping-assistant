# Reliability

What keeps ARIA up, how you'd know it isn't, and what it costs in time. The operator runbook is [operations](guides/operations.md).

## Alarms

`OperationsStack` (`packages/infra/stacks/operations_stack.py`) creates these. Unless the table says otherwise, an alarm needs 2 of 2 five-minute periods and treats missing data as not breaching. Log-event alarms count matches with `Sum`. Prod requires `operations.alarmTopicArn` so they notify someone.

| Alarm | Fires on | Threshold |
| --- | --- | --- |
| `ApiInvocationErrors` | API log events `runtime invocation failed` | ≥ 3 |
| `AgentErrors` | Runtime log events `agent_turn_failed` with `retryable` true: failures the service owns, not a caller's invalid request, another shopper's session or a spent turn budget | ≥ 3 |
| `SessionBuildFailures` | Runtime log events `session_build_failed`; every turn in such a session fails | ≥ 2 in one period |
| `ModelThrottles` | `agent_turn_failed` with `code = "throttled"` | ≥ 1 in one period |
| `ToolDenials` | Runtime log events `tool_denied` for a tool the agent isn't allowed or oversized arguments (not a spent call budget): a prompt-injection signal | ≥ 5 in one period |
| `SearchFailures` | Runtime log events `product_search_failed`: the catalog backend, its alias or its credential failed, and the shopper got an error instead of products | ≥ 3 in one period |
| `SearchEmbeddingFailures` | Runtime log events `product_search_embedding_failed`, with the `opensearch` provider: searches fell back to keyword search, so nothing else reports a lost embedding model | ≥ 3 in one period |
| `UnhealthyApi` | Unhealthy API targets behind the load balancer | ≥ 1 |
| `ApiHealthyHosts` | Fewest healthy API tasks per minute below `api.minTasks`; missing data breaches, so no tasks at all alarms | 3 of 5 minutes |
| `AlbServerErrors` | Target 5xx responses at the load balancer | ≥ 5 |
| `AlbLoadBalancerErrors` | 5xx from the load balancer itself (no healthy task, refused connections, idle timeout) | ≥ 5 in one period |
| `WafRateLimitBlocks` | WAF blocks from the per-IP rate rule (`api.wafRateLimit`) | ≥ 1 in one period |
| `InterceptorDenials` | Interceptor log lines `interceptor: target invocation denied`: a failed actor or token check, or a method the agent doesn't use. The agent's own calls always pass, so one refusal means a configuration error or the agent's token in use elsewhere | ≥ 1 in one period |
| `InterceptorErrors`, `InterceptorThrottles` | Gateway interceptor Lambda errors (unexpected failures, and refusals of a message with no id to answer) and throttles | ≥ 3, ≥ 1 |
| `SavedItemsErrors`, `SavedItemsThrottles` | Saved-items sample Lambda, when deployed | ≥ 3, ≥ 1 |
| `SearchRed`, `SearchDiskLow`, `SearchSnapshotFailure` | OpenSearch health, when the example domain is deployed | red, < 10 GiB free, any failure |

The dashboard, `<ns>-operations`, shows these alarms, conversation failures, p95 turn duration and model token usage. The alarm names are `<ns>-<Alarm>`.

No alarm watches a Gateway target's own backend; give each target its own alarms. An interceptor refusal counts in `InterceptorDenials`, and an unexpected interceptor failure in `InterceptorErrors`.

## Budgets and timeouts

| Limit | Value | Where |
| --- | --- | --- |
| One turn, end to end | `api.requestTimeout`, 120 s at most (the load balancer idles out at 150 s; the browser gives up at 130 s) | API, agent deadline |
| Turns per conversation | 100 (`runtime.maxSessionTurns`, 1–500) | Agent |
| Tool calls per turn | 8 (`runtime.maxToolCalls`, 1–20). The first batch over budget is refused and the model answers from what it has; if it asks again, the turn ends | `ToolSafety`, `packages/agent/src/core/safety.py` |
| Tool argument and result size | 8 KiB in, 64 KiB out | `ToolSafety` |
| Model retries on throttling | 3 attempts, 1 s then 2 s apart; botocore adds one retry per attempt | `SessionBuilder`, `packages/agent/src/core/builder.py` |
| Gateway MCP start-up | 10 s; each request 10 s to connect, 30 s to read | `SessionBuilder._build_mcp_client()`, `core/transport.py` |
| AgentCore Identity and SSM calls | 3 s connect, 10 s read, 3 attempts | `core/downstream.py`, `core/config.py` |
| Tool-result screening (ApplyGuardrail) | 3 s connect, 10 s read, 2 attempts; a failure blocks the result | `GuardrailScreen` |
| HTTP catalog adapter | 10 s timeout, 256 KiB and 20 products per response, no redirects | `packages/catalog` |
| OpenSearch search | 10 s timeout, one retry | `get_client()`, `packages/catalog/src/aria_catalog/search.py` |
| Query embedding (Embed v4) | 1 s connect, 3 s read, one attempt; on any failure the search runs by keyword | `query_client()`, `packages/catalog/src/aria_catalog/embedding.py` |
| Indexing embedding | 96 texts per call; adaptive retries (8 attempts) inside 6 job attempts, backing off 2 s to 60 s on throttling; the task has no time limit | `packages/indexer` |
| Search results | `limit` capped at 20 | `search_products` |

## Latency

Measured on Dev; details in [streaming and latency](design-docs/streaming-and-latency.md):

- First text of a later turn: about 1.6 s.
- First text of a session's first turn: about 3.5 s, including about 1.4 s of session build.
- Local search without a query embedding: under 10 ms. A hybrid OpenSearch search adds one Embed v4 call, which gives up after about 4 s; the [live checks](guides/testing.md#live-checks-after-a-deploy) measure it. A Gateway tool call: 0.15–0.6 s.
- The guardrail's `sync` mode adds about 0.9 s to each model call compared with `async`.

## Failure modes

| Failure | What the shopper sees | Detected by |
| --- | --- | --- |
| The model call fails or is throttled | An error message in the chat; the turn can be retried | `AgentErrors` |
| The guardrail blocks input or output | The configured blocked message | Guardrail metrics in CloudWatch |
| A Gateway tool fails, its credential can't be fetched, or the interceptor denies it | That call fails alone and the agent explains; the session and its other tools keep working; failed tool calls are hidden | `InterceptorDenials` for refusals and `InterceptorErrors` for interceptor failures; the agent logs `gateway_http_error` when the Gateway answers with an HTTP error; target failures need the target's own alarms |
| The Gateway or the token mint is unavailable when a session starts | An error on every turn until it recovers; the build is retried on the next turn | `SessionBuildFailures` |
| The model keeps calling tools | The model answers from the results it has; if it asks for tools again, the turn ends there with a short notice | `tool_budget_ended_turn` (budget denials are left out of `ToolDenials`) |
| The shopper stops an answer and asks again | The new message runs at once: it cancels the turn still running in the Runtime, which keeps going after its caller leaves | `agent_turn_superseded`; `busy` only if the older turn can't stop within 10 s |
| One client IP sends too much | WAF blocks it for the rest of the 5-minute window | `WafRateLimitBlocks` |
| The API's targets are unhealthy | Requests fail at the load balancer | `UnhealthyApi`, `ApiHealthyHosts`, `AlbLoadBalancerErrors` |
| OpenSearch is red or full | Search errors | `SearchRed`, `SearchDiskLow` |
| The query embedding fails or is slow | Results from the keyword search alone, after at most about 4 s | `SearchEmbeddingFailures`; `product_search_embedding_failed` in the Runtime log |
| The alias has no index (a first deploy before the first load) | Search errors | `SearchFailures`; `product_search_failed` in the Runtime log |
| An indexing run fails | Nothing: the alias stays on the previous version | The script's exit code and the manifest's `error`; the unpromoted index stays until an operator deletes it ([operations](guides/operations.md#promote-roll-back-and-clean-up)) |

## Scaling

- The API runs on ECS Fargate between `api.minTasks` and `api.maxTasks`; Prod requires at least 2.
- AgentCore Runtime runs each session in its own microVM and scales with sessions.
- The Gateway's Lambdas have reserved concurrency: 20 for the interceptor, which every Gateway tool call passes through, so 20 caps a stage's concurrent Gateway calls; 10 for the saved-items sample. Raise the interceptor's in `GatewayStack` before a load test or a launch.
