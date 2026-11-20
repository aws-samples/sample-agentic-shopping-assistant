# ARIA: map for agents

ARIA is a shopping assistant that customers deploy and extend. A Nuxt storefront (NORDLY) and a FastAPI service sit in front of a Strands agent (Aria) on Amazon Bedrock AgentCore Runtime. Frequent tools run in the agent; the rest run behind AgentCore Gateway. One CDK app deploys the backend; the storefront is a static SPA you host.

This file is a map. Read the doc for your task, not all of them.

## Start here

- [ARCHITECTURE.md](ARCHITECTURE.md): packages, layering rules, stacks, a turn end to end.
- [Core beliefs](docs/design-docs/core-beliefs.md): the principles. A change that breaks one needs a design doc.

## Where to look

| You're working on | Read |
| --- | --- |
| Agent behavior, prompt, tools | [packages/agent/AGENTS.md](packages/agent/AGENTS.md), [shopping conversation](docs/product-specs/shopping-conversation.md) |
| A new local tool | [Add a local tool](docs/guides/add-a-local-tool.md), [where a tool belongs](docs/design-docs/tool-placement.md) |
| A customer API, MCP server or Lambda behind the Gateway | [Gateway onboarding](docs/guides/gateway-onboarding.md), [Gateway notes](docs/references/agentcore-gateway-notes.md) |
| Identity, tokens, headers | [Identity and tokens](docs/design-docs/identity-and-tokens.md), [SECURITY.md](docs/SECURITY.md) |
| Search and the catalog | [Catalog search design](docs/design-docs/catalog-search.md), [product search design](docs/design-docs/product-search.md), [search spec](docs/product-specs/product-search.md) |
| A customer's catalog: definition, providers, indexing | [Bring your own catalog](docs/guides/integrations.md#bring-your-own-catalog), [catalog runbook](docs/guides/operations.md#catalog-runbook), [packages/indexer/AGENTS.md](packages/indexer/AGENTS.md) |
| The web app | [packages/web/AGENTS.md](packages/web/AGENTS.md), [FRONTEND.md](docs/FRONTEND.md), [DESIGN.md](docs/DESIGN.md) |
| Infrastructure and configuration | [packages/infra/AGENTS.md](packages/infra/AGENTS.md), [configuration reference](docs/generated/config-reference.md) |
| Deploying | [Release](docs/guides/release.md), [deployment safety](docs/design-docs/deployment-safety.md) |
| Deciding a stage's settings before launch | [Launch settings](docs/guides/launch-settings.md) |
| Running a stage | [Operations](docs/guides/operations.md), [RELIABILITY.md](docs/RELIABILITY.md) |
| Testing | [Testing](docs/guides/testing.md) |
| Setting up and running locally | [Getting started](docs/guides/getting-started.md) |
| Configuration: IdP, catalog, Memory, guardrail | [Integrations](docs/guides/integrations.md), [memory](docs/design-docs/memory.md) |
| Evaluations | [Evaluations](docs/guides/evaluations.md) |
| Rotating credentials, rolling back | [Operations](docs/guides/operations.md#rotate-credentials), [release](docs/guides/release.md#roll-back) |
| How answers and cards render | [Response layout](docs/design-docs/response-layout.md), [product cards](docs/product-specs/product-cards.md) |
| Anything else | [Guides](docs/guides/index.md), [design docs](docs/design-docs/index.md) |
| What's weak or unfinished | [QUALITY_SCORE.md](docs/QUALITY_SCORE.md), [tech-debt tracker](docs/exec-plans/tech-debt-tracker.md) |
| Bigger work | [PLANS.md](docs/PLANS.md) |
| Product judgment | [PRODUCT_SENSE.md](docs/PRODUCT_SENSE.md), [product specs](docs/product-specs/index.md) |
| Third-party behavior | [References](docs/references/index.md) |

## Rules

1. **The shopper's token never leaves the agent.** Tools get identity from verified context: `agent.state` locally, `x-aria-actor-id` behind the Gateway. Never a tool argument, never a forwarded token.
2. **Enforce in code, not in prompts.** Allowlists, budgets, ownership checks, the guardrail and Cedar are the controls. The prompt only guides.
3. **No public endpoints** without explicit approval: no `AuthType: NONE` function URL, no `Principal: "*"`, no open ingress, no unauthenticated route.
4. **Deploy only from a reviewed plan,** and never change production without explicit direction.
5. **Tests never call AWS.** Live invocations, deploys, data writes and deletions need explicit authorization.
6. **Keep the docs true in the same change:**
   - When you change a guide, update its Korean twin (`.ko.md`) too.
   - Design docs keep their `Status:` line current.
   - Never hand-edit `docs/generated/`; regenerate it.
7. **Commit no secrets, tokens or customer configuration.** Real `.toml` files stay local (gitignored). `packages/infra/cdk.context.json` names the target account: commit it only on the branch you deploy from, never to `main` ([release](docs/guides/release.md#manual-deployment)).

## Commands

| Command | What it does |
| --- | --- |
| `pnpm check` | Every local gate: Python packages, web tests and build, docs checks |
| `(cd packages/<pkg> && uv run --frozen pytest)` | One package's tests; the Gateway Lambdas are `mcp-servers/gateway-interceptor` and `mcp-servers/saved-items-lambda`, and the indexing job is `indexer` |
| `uv run --project packages/infra --frozen python scripts/gen_docs.py` | Regenerate `docs/generated/` |
| `python3 scripts/check_docs.py` | The docs checks alone |
| `ARIA_CONFIG=<toml> sh scripts/plan_deployment.sh` | Plan a deployment (read-only) |
| `ARIA_CONFIG=<same toml> ARIA_ALLOW_DEPLOY=yes sh scripts/deploy.sh` | Deploy the reviewed plan |
| `ARIA_CONFIG=<toml> sh scripts/index_catalog.sh --catalog <file> --version <v>` | Validate a catalog against the stage's definition (dry run); `--apply` indexes it on the stage |

## Docs layout

| Folder | Holds | Language |
| --- | --- | --- |
| `docs/design-docs/` | How and why it's built; each doc has a status | English |
| `docs/product-specs/` | What the shopper gets; each spec has a status | English |
| `docs/guides/` | How-tos for customers and operators | English, with `.ko.md` twins |
| `docs/references/` | Verified third-party behavior; upstream `llms.txt` files | English |
| `docs/generated/` | Generated from code; don't edit | English |
| `docs/exec-plans/` | `active/`, `completed/`, the tech-debt tracker | English |

The top level of `docs/` holds `DESIGN.md`, `FRONTEND.md`, `PLANS.md`, `PRODUCT_SENSE.md`, `QUALITY_SCORE.md`, `RELIABILITY.md` and `SECURITY.md`.
