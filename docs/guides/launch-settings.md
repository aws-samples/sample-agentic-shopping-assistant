# Launch settings

[한국어](launch-settings.ko.md)

The settings a stage's owner decides before shoppers reach it, grouped by what they decide. Each is a key in the stage's `.toml`. Start from the Prod template, `packages/infra/config/prod.toml`; the [configuration reference](../generated/config-reference.md) has every key with its default and checks. Stage `Prod` also enforces some choices whatever the settings say ([what Prod enforces](#what-prod-enforces)), and a few decisions have no setting at all ([decisions no setting covers](#decisions-no-setting-covers)).

## Who can reach it

| Setting | Decides | Dev template | Prod template | Weigh |
| --- | --- | --- | --- | --- |
| `api.allowIpList` | The networks the load balancer admits | `127.0.0.1/32` | A placeholder `/24` | A public storefront needs `0.0.0.0/0` (and `::/0`), with `api.allowPublicIngress` |
| `api.allowPublicIngress` | Whether Prod admits a network broader than /8 (IPv6: /32) | `false` | `false` | `true` for a public storefront; WAF and the per-IP rate limit then are the edge |
| `api.wafRateLimit` | Requests one IP may send in 5 minutes before WAF blocks it | 100 | 100 (default) | Shoppers behind one NAT share an IP, and there's no per-shopper limit |
| `api.certificateArn`, `api.corsAllowOrigins` | HTTPS on port 443, and the storefront origins the API answers | None; `http://localhost:4000` | Placeholders | The certificate must be issued and in the stage's Region; preflight checks both |

## Sign-in

| Setting | Decides | Dev template | Prod template | Weigh |
| --- | --- | --- | --- | --- |
| `auth.provider` | Managed Cognito, or your own OIDC IdP | `cognito` | `cognito` | With `oidc`, MFA, the login pages and threat protection are your IdP's |
| `auth.mfa` | Multi-factor sign-in with an authenticator app (managed Cognito) | `optional` | `optional` | `optional` asks only shoppers who registered an authenticator, and nothing offers registration; `required` makes every shopper register one at sign-in |
| `auth.domainPrefix` | The managed login domain, `<prefix>.auth.<region>.amazoncognito.com` | Empty: `<namespace>-<account ID>` | Empty | Set it before the first deploy: it can't change in place, and the default shows the account ID |
| `auth.callbackUrls`, `auth.logoutUrls` | Where managed login returns the shopper | `localhost:4000` | Placeholders | Your storefront's HTTPS URLs |
| `auth.tenantClaim` | The claim that separates tenants in the actor ID | Empty | Empty | Changing it later gives every shopper a new actor ID, and their history isn't found |

## Catalog and data residency

| Setting | Decides | Dev template | Prod template | Weigh |
| --- | --- | --- | --- | --- |
| `tools.productSearch.provider` | Where search reads the catalog | `fixture` | `http` | `opensearch` creates a domain in the VPC and an indexing job; `http` calls your API; Prod rejects `fixture` |
| `tools.productSearch.catalogFile` | Your catalog definition | The example | The example | Every catalog maps the fashion-shaped card fields; the definition is capped at 16 KB |
| `tools.productSearch.endpoint`, `secretArn` | Your search API and its credential, with `http` | None | Placeholders | The secret holds a header and its value |
| `[catalog.embedding].model`, in the definition | The Embed v4 model or inference profile, with `opensearch` | `global.cohere.embed-v4:0` | The same | `global` can process catalog text and shoppers' queries outside the Region; in Seoul it's the only option |

## Safety

| Setting | Decides | Dev template | Prod template | Weigh |
| --- | --- | --- | --- | --- |
| `guardrail.mode` | Whether the guardrail blocks or only reports | `ENFORCE` | `ENFORCE` | Prod requires `ENFORCE`; `AUDIT` measures on Dev first |
| `guardrail.streamProcessing` | When a reply is checked | `async` | `sync` | `sync` checks before it streams; `async` answers sooner but can show a reply briefly before cutting it |
| `guardrail.screenToolResults` | Screening Gateway tool results before the model reads them | `true` | `true` | Can block valid results, and fails closed when throttled |
| `policy.enabled`, `policy.mode` | Cedar on Gateway calls | Off, `LOG_ONLY` | On, `ENFORCE` | Every target needs a `policyFile`; try `LOG_ONLY` on Dev first |
| `runtime.maxToolCalls`, `runtime.maxSessionTurns` | One turn's tool calls, and one conversation's turns | 8, 100 (defaults) | 8, 100 (defaults) | The agent refuses past either |

## Cost and capacity

| Setting | Decides | Dev template | Prod template | Weigh |
| --- | --- | --- | --- | --- |
| `runtime.modelId`, `runtime.maxTokens` | The model, and the longest reply | `global.openai.gpt-5.6-luna`, 2048 | The same, 2048 (default) | A cross-Region profile can process prompts outside the Region; there's no fallback model |
| `api.minTasks`, `api.maxTasks` | API tasks | 1 to 3 (defaults) | 2 to 6 | Prod requires at least 2 |
| `vpc.vpcId`, `vpc.natGateways`, `vpc.interfaceEndpoints` | Your VPC or the managed one, its NAT gateways and endpoints | Managed, 1 NAT, `ecr`, `ecr-docker`, `logs` | Managed, 3 NATs (default) | Adding `bedrock` takes model and embedding calls off the NAT gateway |
| `evaluations.enabled`, `evaluations.samplingPercentage` | Online evaluations, and the share of sessions they judge | Off | Off | Each judged session costs judge-model tokens, and needs `operations.captureContent` |

## Privacy

| Setting | Decides | Dev template | Prod template | Weigh |
| --- | --- | --- | --- | --- |
| `memory.longTerm` | Derived facts and preferences kept per shopper | Off | Off | Turn on only after a privacy review |
| `memory.eventExpiryDays` | How long conversation events stay | 90 | 90 (default) | 7 to 365 |
| `operations.captureContent` | Prompts and replies in traces | Off (default) | Off (default) | Puts shoppers' text in the trace logs |

## Operations

| Setting | Decides | Dev template | Prod template | Weigh |
| --- | --- | --- | --- | --- |
| `operations.alarmTopicArn` | Where alarms and their recoveries go | None | A placeholder | Prod requires it |
| `app.account`, `app.region` | Where the stage deploys | Your AWS CLI's | Explicit | Prod requires both; the Region decides which Embed v4 profiles, Nori package and AgentCore Availability Zones there are |

## What Prod enforces

Stage `Prod` applies these whatever the settings say:

- HTTPS, explicit HTTPS CORS origins and sign-in URLs, at least two API tasks, an alarm topic and one NAT gateway per AZ.
- The guardrail on and in `ENFORCE`, and Cedar in `ENFORCE` with a managed Gateway. No fixture catalog and no saved-items sample.
- Cognito's Plus plan with threat protection, billed per monthly active user, and deletion protection on the user pool.
- Termination protection on every stack. Memory, the user pool, the OpenSearch domain, the buckets and the log groups are kept when their stack or resource goes.
- With `opensearch`, a domain of three `r6g.large` data nodes across three AZs, three dedicated masters and one replica.

## Decisions no setting covers

The [tech-debt tracker](../exec-plans/tech-debt-tracker.md) holds the details.

- A session whose Gateway connection or token mint fails doesn't start, local search included (#1).
- One model serves every turn: when Bedrock fails it, every turn fails (#10).
- The Runtime serves its latest version, with no canary; rolling back means redeploying the previous release (#2).
- Gateway tools that change data run without the shopper's confirmation; the shipped tools only read (#3).
- A shopper's access token can call the Runtime directly, around the API's WAF and checks (#4).
- No per-shopper quota: one account can spend a stage's model budget (#5).
- No synthetic canary: an outage between turns goes unnoticed until someone chats (#19).
