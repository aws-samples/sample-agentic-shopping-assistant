# Getting started

[한국어](getting-started.ko.md)

## Set up

Requires Node 24, pnpm 10.18.2 and uv. Each Python package pins its version in `.python-version`, and uv installs it if it's missing: 3.14 for the agent, API, infra and indexer (the image runtime), and 3.13 for the two Lambda packages (the Lambda runtime). Docker or Finch is needed only for container builds and real CDK asset bundling.

```sh
pnpm install --frozen-lockfile
pnpm check
```

Checks use synthetic identities, fixtures, mocked transports and offline CDK synthesis. They do not invoke AWS services, and they pass on a fresh clone before you own any AWS resources.

Then select a configuration:

```sh
cp packages/infra/config/dev.toml packages/infra/.toml
```

The default template uses managed Cognito and the packaged example catalog, 150 synthetic products searched in memory (`packages/catalog/src/aria_catalog/example/`), and leaves long-term memory and evaluations off, so it needs no product dataset or OpenSearch domain. To search your own catalog, write a catalog definition, set `tools.productSearch.catalogFile` to it and choose `opensearch` or `http` ([bring your own catalog](integrations.md#bring-your-own-catalog)). The gitignored active `.toml` is never overwritten by the tools; use `ARIA_CONFIG` to select a different file. `config/prod.toml` contains explicit placeholders, enforced authorization, TLS and availability settings — replace its account, DNS, certificate, notification, ingress and catalog values.

## Run the web app against a deployment

Everything except the web app runs in AWS. Deploy first (see [release](release.md)), then serve the SPA from your machine:

```sh
cp packages/web/env/local.env packages/web/.env   # then fill in the values below
pnpm --filter nordly-web dev                       # http://localhost:4000
```

Fill in `packages/web/.env` from the stack outputs. `OIDC_ISSUER` is `DiscoveryUrl` without `/.well-known/openid-configuration`, and `OIDC_CLIENT_ID` is `UserPoolClientId`. `API_URL` is `http://<ALBDns>:<serverPort>`, or your HTTPS API host when `api.certificateArn` is set.

- The API load balancer admits only `api.allowIpList`. Add your public IP as a `/32` and redeploy. The Dev template's `127.0.0.1/32` admits nobody, and a new network (VPN, office, home) means updating the list.
- Without `api.certificateArn`, the API is served over plain HTTP, so access tokens cross the internet unencrypted. Use test accounts only, or configure a certificate. Some networks also block port 8000 or buffer a plain-HTTP stream.
- Open the app at exactly `http://localhost:4000`. The Cognito callback and the API's CORS origin default to that address.

Traces and session metrics are in AgentCore Observability: the observability page in the AgentCore console, or generative AI observability in CloudWatch. Spans need CloudWatch Transaction Search; see [operations](operations.md). To build the images on their own, use the `packages/` context documented in [container builds](release.md). Per-package detail is in [`packages/web/README.md`](../../packages/web/README.md) and [`packages/api/README.md`](../../packages/api/README.md).

## Renaming the storefront

The brand appears in code and documentation. These locations affect behavior:

- `packages/agent/src/prompts/system.md` — the store identity the agent is given, asserted by `packages/agent/tests/unit/test_builder.py`
- `packages/catalog/src/aria_catalog/example/catalog.toml` — the example catalog's name and the search tool's description, which the model reads when choosing to search
- `packages/infra/stacks/agent_runtime_stack.py` — the Memory resource description, which is a deployed resource property
- `packages/web/package.json` — the `nordly-web` package name, which `scripts/check.py` filters on
- `packages/web/nuxt.config.ts` and `packages/web/env/local.env` — the `BRAND_NAME` default

The rest is display copy and documentation. `git grep -i nordly` finds every occurrence.

## Deployment boundaries

`pnpm check` and `scripts/build_images.py` perform local work only. `scripts/preflight.py` makes read-only AWS dependency checks. `scripts/plan_deployment.sh` adds local synthesis and a read-only diff for operator review. `scripts/smoke.py --invoke` invokes the model and writes Memory events. `scripts/deploy.sh` changes AWS resources and requires separate operator authorization. No deployment or smoke invocation is part of local validation.

The repository includes a production configuration path, but passing local checks is not production readiness. Before serving real traffic you need public HTTPS with a delegated DNS name and certificate, your own IdP and catalog authorization, alarm notification routing and an incident exercise, representative load and safety evidence with agreed quotas and cost budgets, and a rehearsed restore/rollback with recorded RPO/RTO. The gate-by-gate evidence checklist is in [release](release.md#acceptance-before-production-traffic).
