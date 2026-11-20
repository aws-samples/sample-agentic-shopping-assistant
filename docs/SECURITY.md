# Security

The rules ARIA's security depends on, and where each is enforced. The identity model is explained in [identity and tokens](design-docs/identity-and-tokens.md).

## Identity

- **The shopper's token is verified twice and never forwarded past the agent.** The API verifies it and passes it to the Runtime, which verifies it again. The agent uses it only for the actor ID and session ownership.
- **Sessions belong to their shopper.** The session ID is bound to the principal, and both the API and the Runtime check it on every turn (`packages/contracts`).
- **The Gateway accepts only the agent's own token.** By default that's an on-behalf-of token: `aud` is `aria-tools`, `sub` is the shopper, `act.sub` is the agent. A shopper's token is rejected with 403.
- **The actor ID is set by code and checked by the interceptor against the agent's token.** On every tool call the interceptor requires the token to come from the agent's client (`act.sub` for on-behalf-of, `client_id` for m2m) and, for on-behalf-of, the header to match the token's subject. Targets get the actor ID as `x-aria-actor-id`, and must scope their data to it. Identity is never a tool argument; the loader rejects an OpenAPI document that declares the header as a parameter.
- **A forwarded header can't pose as the agent's own.** `gateway.forwardHeaders` can't name `Authorization`, `auth` or an `x-aria-*` header, in any casing: the loader rejects them, and the agent refuses to start with one in `FORWARD_HEADERS`. A forwarded header carries the caller's value to tools.
- **The interceptor fails closed.** It denies every request while `GATEWAY_AUTH_FLOW` or `AGENT_CLIENT_ID` is unset, and any batch or MCP method the agent doesn't use.

## Tools

- **Only allowlisted tools are exposed.** `ALLOWED_TOOLS` is derived from configuration, and `ToolSafety` enforces it with a per-turn budget and an argument size limit.
- **Every Gateway target authenticates with its own credentials:** an IAM role, OAuth client or API key. The interceptor never returns `Authorization`, which would replace them.
- **Cedar policies authorize each tool action** when `policy.enabled = true`. Prod requires `ENFORCE` and a policy file for every customer target.
- **Treat tool output and retrieved Memory as untrusted input.** They're context, never instructions. With `guardrail.screenToolResults` (on by default), the guardrail screens each Gateway tool result before the model reads it, and a result it blocks or can't check becomes an error.
- **Search arguments are checked against the catalog definition.** The model fills only the parameters the definition declares. `aria_catalog` refuses an unknown parameter, an undeclared value, a wrong type and oversized input, and builds the query itself, so the model never chooses an index, a field, a URL or a query body. The definition is configuration, validated at synth.
- **The agent's Gateway token goes only to the Gateway.** The transport sends it only to the configured Gateway origin, which must be an AgentCore Gateway host (`<id>.gateway.bedrock-agentcore.<region>.amazonaws.com`, checked by the agent and at synth), and follows no redirects.

## Infrastructure

- **No public endpoints** without explicit approval: no Lambda function URL with `AuthType: NONE`, no Lambda permission for `Principal: "*"`, no ingress from `0.0.0.0/0`, no unauthenticated API route. The API's load balancer admits only `api.allowIpList`; in Prod an entry broader than /8 (IPv6: /32) needs `api.allowPublicIngress = true`. WAF limits each client IP to `api.wafRateLimit` requests in 5 minutes. Review every plan for these ([deployment safety](design-docs/deployment-safety.md)).
- **The Gateway's role trusts only this stage's Gateway** (`aws:SourceArn` pinned to `gateway/<ns>-tools-*`).
- **Least privilege, named resources.** Roles grant named resources. Where AgentCore or CDK generates a suffix, grants use the narrowest prefix, and each such exception is listed with its reason in [cdk-nag exceptions](generated/cdk-nag-exceptions.md).
- **The catalog bucket is private.** Every public access block is on, TLS is enforced, objects are S3-encrypted and versioned. Operators write it through `scripts/index_catalog.sh`, and only the indexing task's role reads it, under `catalog/`.
- **The indexing task has its own role and network.** Its role reads only the bucket's `catalog/` prefix, invokes only the definition's embedding model, and may GET, HEAD, PUT and POST only the alias, its versioned indexes, `_aliases` and the alias's search pipeline: it can't delete. Only ECS tasks in the stage's account and region may assume it. Its security group has one TCP 443 rule into the domain's and no ingress, and the task has no endpoint. The Runtime may only search the alias and invoke the embedding model.
- **Secrets stay out of templates and logs.** The agent client's secret reaches AgentCore Identity through a deploy-time custom resource; the Runtime reads only that provider's secret. Catalog credentials live in Secrets Manager.
- **Logs never carry tokens or header values.** The agent, API and interceptor log names, IDs and outcomes only. The search tool logs counts, timings and error types, never arguments or products, and the indexing job's manifest counts rejected products by field, never by value.

## Web app

- **An answer can't make the browser fetch a URL.** The answer renderer (`packages/web/app/utils/markdown.ts`) renders no images, autolinks or links, and no raw HTML, so a prompt-injected answer can't leak the conversation through a URL or offer a lookalike payment link. A Content-Security-Policy meta tag adds `img-src 'self' data:` (plus origins in `NUXT_PUBLIC_CSP_IMG_SRC`), `object-src 'none'` and `base-uri 'none'`. Set `frame-ancestors` and the rest of the policy at the web host.
- **The shopper sees no tool output except product cards.** Only a valid `product.v1` result is kept; nothing else from a tool is stored or shown, and error text from the backend never reaches the page.
- **Tokens stay in `sessionStorage`,** with no client secret in the web app.

## Data

- **Long-term memory is off by default.** Every deployment keeps each conversation in Memory, keyed by actor ID, as events that expire after `memory.eventExpiryDays` (90 days by default). Long-term memory (`memory.longTerm`) derives preferences and facts about each shopper, so turn it on in Prod only after a privacy review. Conversation content capture for evaluations needs explicit consent (`operations.captureContent`). Retention and deletion are in [operations](guides/operations.md#memory-and-pii).
- **The guardrail is on by default.** It filters sexual content, violence, hate, insults, misconduct and prompt attacks, and blocks payment card data, PINs, passwords, AWS keys and Korean resident registration numbers in both directions. Prod requires it in `ENFORCE`; `AUDIT` measures impact on Dev without blocking. Streaming is each stage's choice: `sync`, the default, checks a reply before it streams, and `async` answers sooner but can briefly show text a filter then cuts. It checks the shopper's latest message and each reply, and a blocked message is replaced in history, so a multi-turn attack is judged one message at a time; Gateway tool results are screened on their own.
- **Prod refuses the saved-items sample** (`gateway.savedItemsSample`), whose data is fixture data for any shopper.
- **Prod refuses the fixture catalog** (`tools.productSearch.provider = "fixture"`), whose products are synthetic.
- **Embedding can leave the region.** With `opensearch`, the catalog's embedding text and every shopper's search query go to Cohere Embed v4 through the definition's inference profile. The default `global` profile can process them in any commercial region, and in ap-northeast-2 it is the only one Bedrock offers. Decide residency before choosing `opensearch` ([integrations](guides/integrations.md#korean-text-embeddings-and-residency)).
- **Catalog text is untrusted input.** Local search results, the customer's catalog text, reach the model without the guardrail's tool-result screening ([tracker](exec-plans/tech-debt-tracker.md)).

## Adding a cdk-nag exception

Suppress the narrowest scope that works, a resource rather than a stack, and give a reason that names the resources and why a wildcard is unavoidable. `pnpm check` fails when [the exceptions list](generated/cdk-nag-exceptions.md) is stale; regenerate it with `uv run --project packages/infra --frozen python scripts/gen_docs.py`. The reason is what reviewers will read.

## Reviewing a change

- [ ] No identity in tool arguments; no shopper token in any outbound call.
- [ ] New tools are allowlisted and have a Cedar policy (required in `ENFORCE`, in every stage).
- [ ] The deployment plan shows no public endpoint and no unintended IAM wildcard.
- [ ] Logs added by the change contain no token, secret or header value.
- [ ] Nothing a model or tool produces is rendered as HTML, an image or a link.
- [ ] Denials are tested as well as successes.
