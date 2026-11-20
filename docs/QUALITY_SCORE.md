# Quality score

A grade for each product domain and architectural layer, and the gaps behind it. Update a grade when its evidence changes; the tracker holds the details of each gap ([tech-debt tracker](exec-plans/tech-debt-tracker.md)).

| Grade | Means |
| --- | --- |
| A | Unit or synth tests, and verified end to end on a deployed stage in the current release |
| B | As A, with known gaps that affect shoppers or operators |
| C | Unit or synth tests only, or verified live but with significant gaps |
| D | Untested, or known not to work |

Grades rest on the tests, on end-to-end runs on a Dev stage, and on a run of the Prod profile in ap-northeast-2.

## Product domains

| Domain | Grade | Evidence | Gaps |
| --- | --- | --- | --- |
| Shopping conversation | B | Agent unit tests, including real Strands turns with a scripted model; Dev browser runs (10 turns: six tool families, Stop then a new message, injection and card-number refusals); dataset evaluations on Dev (example set: goal success 1.00, instruction following 1.00; safety set: goal success 1.00 on all six cases); in the Prod profile in ap-northeast-2, `sync` streaming answered a card-number request with the guardrail's own message before any reply streamed | A failed Gateway connection stops the session (#1); no model fallback (#10); first-turn latency (#9); an order-status answer was refused by the guardrail late in a long Dev conversation, not reproduced alone (#11) |
| Product search, fixture provider | C | Catalog and tool unit tests on the example catalog: lexical scoring, filters and ranges, cards, the schema generated from the example definition | Not run on a deployed stage |
| Product search, `http` provider | B | Catalog and tool unit tests against a faked HTTPS catalog: the flat request in parameter names, products mapped through the definition's card, the credential cache and refetch; in the Prod profile in ap-northeast-2, through the agent, against a stub catalog API that requires an API key: the Runtime read the key from Secrets Manager, called the API through the NAT gateways, and mapped its products through the definition's card | Run against a stub, not a customer's API; Secrets Manager timeouts (#25) |
| Product search, `opensearch` hybrid | B | Catalog, tool and indexer unit tests against fake OpenSearch and Bedrock; synth tests of the domain, the Nori plugin, the catalog bucket, the indexing task and the grants; on Dev, through the agent with the Runtime in VPC mode: hybrid queries fused by RRF, a query sharing no word with the catalog found the rain boots first through the vector half alone, filter-only searches skipped the embedding, Korean compound words matched through Nori, and with the embedding model denied, searches answered by keyword; in the Prod profile in ap-northeast-2, on a domain of three data nodes across three AZs with three dedicated masters and one replica, the same hybrid, vector-only, Nori and filter-only searches, with the catalog embedded through the `global` Embed v4 profile | Definition changes before the load (#13); unpromoted indexes (#14); embedding residency in ap-northeast-2 (#6) |
| Product cards and citations | A | Web unit tests; Dev browser runs; only `product.v1` fields reach the browser | — |
| Saved-items sample | A | Lambda unit tests; Dev runs through the agent and directly; on Dev it returned cards from the example catalog | — |
| Sign-in and sessions | B | API, contract and web store tests; Dev browser runs with managed login; in the Prod profile in ap-northeast-2, managed login over HTTPS with the Plus plan, threat protection and optional MFA | Login pages unbranded (#16) |

## Gateway and identity

| Area | Grade | Evidence | Gaps |
| --- | --- | --- | --- |
| Agent's token, `on_behalf_of` with managed Cognito | A | Unit tests; token claims verified on Dev and in the Prod profile in ap-northeast-2 | — |
| Agent's token, `m2m`, BYO IdP, external Gateway | C | Synth and loader tests | Not exercised live |
| Interceptor checks (actor, agent-token binding, methods) | A | Unit tests; on Dev the agent's calls pass, another shopper's actor or none is refused with a JSON-RPC access-denied error, and targets receive the agent's trace ID as `x-correlation-id`; the same in the Prod profile in ap-northeast-2 | — |
| Lambda targets | A | Dev, through the agent and directly; a customer-style target in the Prod profile in ap-northeast-2, through the agent and directly | — |
| API Gateway targets, IAM role and API key | A | Dev, through the agent and directly | — |
| OpenAPI targets, inline with API key and S3 with OAuth | A | Dev, through the agent and directly | — |
| MCP server targets, OAuth | A | Dev, through the agent and directly | — |
| MCP server targets, API key | C | Synth tests | Not exercised live (#18) |
| Target grants `AUTHORIZATION_CODE`, `TOKEN_EXCHANGE` | D | — | Not supported: the loader rejects `AUTHORIZATION_CODE`, and `TOKEN_EXCHANGE` isn't exposed (#17) |
| Cedar policies | B | Synth and loader tests; on Dev in `ENFORCE`, the sample policies permitted a call within their limit, denied a bulk one, and `tools/list` listed only permitted tools; in the Prod profile in ap-northeast-2, a customer target's policy permitted a call within its limit, and Cedar denied one outside it by default | `LOG_ONLY` not run live (#18) |

## Platform layers

| Layer | Grade | Evidence | Gaps |
| --- | --- | --- | --- |
| Agent (`packages/agent`) | B | 284 unit tests; Dev runs | #1, #9, #10, #21, #23 |
| API (`packages/api`) | A | 101 unit tests; Dev runs | — |
| Web (`packages/web`) | B | 103 unit tests and a production build; Dev browser runs | No component tests (#26) |
| Catalog (`packages/catalog`) | B | 542 unit tests: the definition's validation, snapshots of the example's tool schema, index, pipeline and query, the providers, Embed v4 calls and the example catalog; on Dev, the example definition produced the tool, the index, the pipeline, the queries and the cards | #8, #15 |
| Indexer (`packages/indexer`) | B | 26 unit tests against fake OpenSearch, Bedrock and S3, including that every request stays on the paths the task role grants; on Dev, the task loaded the example as two versions, moved the alias each time, and refused a version that existed; in the Prod profile in ap-northeast-2, it loaded the example with one replica on a three-AZ domain | #14 |
| Contracts (`packages/contracts`) | A | 89 contract tests; reference values shared with the interceptor | #28 |
| Gateway Lambdas (`packages/mcp-servers`) | A | 65 interceptor and 16 saved-items tests; Dev runs | — |
| Infrastructure (`packages/infra`) | B | 301 synth and configuration tests; Dev deploys from reviewed plans, including the `Opensearch` stack with its Nori plugin, catalog bucket and indexing task, and the Runtime in VPC mode; in the Prod profile in ap-northeast-2: all eight stacks under termination protection, HTTPS only, two API tasks in two AZs, one NAT gateway per AZ, WAF, the three-AZ domain with dedicated masters and Nori, and preflight's checks of the AZ IDs, the models, the Nori package and the certificate | The definition's size limit (#15); strong cross-stack exports (#27) |
| Operations | B | Alarms for outages, failed session builds, throttling, tool denials, failed searches and WAF blocks, deployed on Dev; the interceptor-denial alarm fired and cleared on Dev from real refusals, and `SearchEmbeddingFailures` fired on Dev from denied query embeddings; alarm and OK notifications reached an SNS subscriber on Dev; turn logs carry per-turn usage with prompt-cache tokens; in the Prod profile in ap-northeast-2, all 18 alarms notify the alarm topic | No canary (#19); `SearchFailures` hasn't fired on a stage |
| Memory | A | Unit and synth tests; on Dev, a conversation outlived its Runtime session, history and the session list read it, long-term extraction and retrieval worked, turning long-term off kept the history, and `DELETE /v1/memory` removed a shopper's events, preferences and facts with no emptied session left listed | — |
| Documentation | B | Link, index, twin parity (headings, code blocks, table rows), label and generated-doc checks in `pnpm check` | Korean twins not reviewed by a native speaker |
