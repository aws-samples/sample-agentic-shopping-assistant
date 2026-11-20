# Release and acceptance

[한국어](release.ko.md)

The repository provides local validation gates and the scripts a delivery pipeline would call. It does not ship a pipeline definition — wire these gates into your own CI, and configure the runner trust, protected branches and tags, credentials and environment approvals there. No deployment is automatic.

## Local gates

From the repository root:

```sh
pnpm install --frozen-lockfile
pnpm check
pnpm audit --audit-level high
CONTAINER_ENGINE=finch pnpm build:images
```

`pnpm check` installs frozen Python environments, runs the API, agent, catalog, indexer, contracts and Lambda tests and the Dev/Prod CDK profile matrix, then the scripts tests, the docs checks (`scripts/check_docs.py`), web contract tests and a production build. Python tests reject escaped botocore network requests; offline synth uses explicit fake account/AZ context and disables asset bundling. These checks do not contact or invoke deployed AWS services.

The image builder creates ARM64 agent, API and indexer archives and a release manifest under gitignored `artifacts/`. Base and collector images are pinned in `build-images.json` and the Dockerfiles; Python and pnpm lockfiles control package versions. Production images exclude development dependencies and install packages during build, not startup. Refresh pins regularly and rerun scans; immutable inputs alone do not establish vulnerability safety.

Scan the three archives and the Lambda dependency manifests with Trivy and fail the build on High/Critical findings — the pinned scanner digest is in `build-images.json`. Dependency audits include build tools. Do not add blanket ignores to make the gate pass. The selected Nuxt version and narrow transitive overrides address the audited lockfile; re-evaluate those overrides when upgrading.

## Pipeline expectations and tags

A pipeline built on these scripts should run validation, image builds, image scans and release tagging, then a saved deployment plan behind a protected manual deployment step. The image build needs an ARM64 runner with a Docker daemon; privileged runner access belongs only on an isolated, trusted runner. The deployment step needs a Docker daemon/socket and short-lived credentials for the explicit target account. Do not expose deployment credentials or sockets to untrusted pull requests.

`scripts/release.py` recognizes explicit `## [X.Y.Z]` or `## [vX.Y.Z]` changelog headings and prints the version to tag; `[Unreleased]` alone produces no tag. Pass `--changed-from <rev>` so that editing Unreleased notes does not retag an older release. If a newly introduced version already has a tag, it must point to the same commit. Configure a protected push identity; never embed tokens into Git remote URLs. Re-run validation, build and scan on the tag before the plan and deployment steps.

Keep scan reports, commit, image IDs, lockfile hashes, configuration fingerprint, approvals and deployment/change-set records together. The provided CDK path builds its own image assets from the selected commit; it is not an ECR digest-promotion system. Reconcile/scan published asset digests before traffic promotion. Add a customer artifact registry/promotion stage if policy requires deploying the exact pre-scanned binary. The builder exports ARM64 images; an X86_64 API configuration needs a matching build/scan job.

## Read-only preflight

Run only with authorization to inspect the selected AWS account:

```sh
ARIA_CONFIG=/absolute/path/to/reviewed.toml \
uv run --project packages/api --frozen python scripts/preflight.py
```

The preflight reads STS identity, model/inference-profile availability, the Availability Zones of the Runtime's subnets, the API certificate, the catalog's search dependencies and, when enabled, the X-Ray trace destination. It rejects account mismatches and template placeholders. With `api.certificateArn`, the certificate must be `ISSUED` and in the stage's Region (`acm:DescribeCertificate`): a DNS-validated certificate is issued only once its validation record resolves from the domain's authoritative name servers, and the load balancer refuses any other at the end of the deploy, with an error about the domain name, signature and key size. It neither invokes a model nor changes AWS.

The Runtime always runs in the VPC, and AgentCore supports VPC connectivity only in the AZ IDs listed under [Supported Availability Zones](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-vpc.md). Preflight fails, naming the AZ ID, if a subnet the Runtime uses is in any other:

- **Existing VPC (`vpc.vpcId`):** the private subnets with egress, the ones the Runtime selects (`ec2:DescribeSubnets`, `ec2:DescribeRouteTables`).
- **Managed VPC:** the region's first three Availability Zones, where the VPC creates its subnets (`ec2:DescribeAvailabilityZones`).

The list is copied into `scripts/preflight.py`; update it when AWS adds zones.

With the `opensearch` provider, preflight also checks the catalog definition's dependencies:

- **Embedding model:** `embedding.model`'s inference profile is `ACTIVE` (`bedrock:GetInferenceProfile`), and the foundation model behind it is authorized and available in the region (`bedrock:GetFoundationModelAvailability`), the same check as the Runtime's model.
- **Nori:** with `analyzer = "nori"`, the region offers the `analysis-nori` plugin for OpenSearch 3.5 (`es:DescribePackages`). Where it doesn't, preflight fails and suggests `standard`.

Its JSON output names the Runtime's zone IDs (`runtimeZoneIds`), the certificate's type (`certificate`, `AMAZON_ISSUED` or `IMPORTED`) and, with `opensearch`, `embeddingModel` and `noriPackage`. SCPs, quotas, target authorization and the full request path still require acceptance checks; an SCP that denies regions must allow the `global` profile's requests ([Embed v4 notes](../references/bedrock-embed-v4-notes.md#permissions)).

The checked-in default model is `global.openai.gpt-5.6-luna`. The shipped Strands `BedrockModel` uses Converse; CDK injects `MODEL_CACHE_ENABLED=false`, and the model builder omits temperature for GPT-5 model IDs. Local tests verify those settings, not account model access or successful live invocation. See [model caching](../design-docs/model-caching.md) for the implemented cache gate.

## Manual deployment

First run `sh scripts/plan_deployment.sh` from a clean checkout with `ARIA_CONFIG` set. It performs read-only preflight, local synthesis and CDK diff with `--change-set=false`; it does not create a change set or deploy. Review the saved assembly, `artifacts/deployment-diff.txt` and `artifacts/deployment-plan.json`, including all IAM changes and replacements.

Only then run `sh scripts/deploy.sh` with `ARIA_ALLOW_DEPLOY=yes`. The deploy script verifies the commit, configuration hash and every assembly file against that reviewed plan, reruns preflight, and deploys that assembly without resynthesis. That explicit opt-in is the approval boundary; interactive CDK prompts are disabled only for this authorized command. If you drive this from CI, keep it a protected manual step and restrict job/artifact access and tag/runner permissions accordingly. Expired or changed plans require a new plan and review.

When a change removes a resource that another stack imports, deploy the importing stacks first: set `ARIA_DEPLOY_STACKS` to their names, separated by spaces ([deployment safety](../design-docs/deployment-safety.md#cross-stack-exports-fix-deploy-order)). The script deploys only those stacks, from the same verified assembly; then run it again without the variable.

Deployment is mutating: do not run it during a review.

With the `opensearch` provider, indexing is a release step of its own. The deploy creates the domain, the catalog bucket and the indexing task, and gives the Runtime and the task the definition, but builds no index. Right after a stage's first deploy, and after any definition change the index must follow, run `scripts/index_catalog.sh` with `--apply` ([operations](operations.md#index-the-catalog)): until the alias points at an index built from the deployed definition, searches fail or miss ([change the catalog definition](operations.md#change-the-catalog-definition)).

Both scripts run CDK with `--lookups false`, so the plan cannot query the account. Before the first plan for a new account or region, record its availability zones (and any existing-VPC lookups) in `packages/infra/cdk.context.json`. Run one synth with lookups enabled, using read-only credentials for the target account. CDK calls `ec2:DescribeAvailabilityZones`, and for an existing VPC it also reads the VPC, subnets and route tables:

```sh
cd packages/infra
ARIA_CONFIG=/absolute/path/to/reviewed.toml ../../node_modules/.bin/cdk synth <ns>Vpc --exclusively
```

Review the file and commit it on the branch you deploy from. The plan requires a clean checkout, so an untracked context file stops it. The file names the target account, which is why this repository does not include one.

These scripts do not bootstrap accounts, configure DNS, enable Transaction Search or import existing resources.

Before the first Prod deploy, decide the settings in [launch settings](launch-settings.md). Prod configuration requires account/region, TLS certificate, explicit network/CORS settings, at least two tasks, an alarm destination and enforced/verified tool authorization, and rejects the fixture catalog. The checked-in Prod template deliberately contains placeholders. In Prod a rolling update starts the replacement tasks before it stops the old ones, so the service briefly runs up to twice its desired task count: leave Fargate quota and subnet addresses for that. Point DNS at the load balancer only after its targets are healthy and the login checks pass.

## Roll back

1. Check out the last good release tag.
2. Plan it with the same reviewed configuration (`scripts/plan_deployment.sh`), and review the diff for replacements and deletions.
3. Deploy that plan.

What a rollback doesn't undo:

- Memory events and records, OpenSearch documents, and index aliases. To go back to an earlier catalog, index its source again as a new version, or point the alias at the older index ([operations](operations.md#promote-roll-back-and-clean-up)). A rolled-back definition needs the catalog indexed again with it.
- Scopes in shoppers' tokens. Tokens keep the scopes granted at sign-in, so if a rollback changes scopes, shoppers may need to sign in again ([sign-in and sessions](../product-specs/sign-in-and-sessions.md#after-a-scope-change)).
- Removed cross-stack exports. An older plan that imports one again may need a staged deploy ([deployment safety](../design-docs/deployment-safety.md)).
- Images. They're rebuilt from the commit, not restored by digest.

The Runtime has no pinned version, so a rollback reaches every new session at once ([tech-debt tracker](../exec-plans/tech-debt-tracker.md)).

## Acceptance before production traffic

Record expected outcomes, owner, evidence and pass/fail for each gate:

| Gate | Required evidence |
| --- | --- |
| Login and refresh | Managed Cognito or the selected BYO IdP completes PKCE, renews access tokens and logs out. Wrong issuer, recipient, signature, scope, expired token and ID-token substitution are rejected. |
| Session isolation | Two users cannot read, invoke, delete or resume each other's session; repeat directly against Runtime and across trusted tenants. Session IDs not bound to the caller, malformed ones included, fail closed. |
| Real model/tool path | Model access is authorized; a Korean streamed conversation successfully calls the configured tool, returns grounded products, and handles empty/error/unknown outputs. Refresh credentials during a continuing session. |
| Tool authorization | Cedar ENFORCE denies an unauthorized action and invalid arguments. New tools stay denied until approved. For an external Gateway, verify equivalent controls before setting `authorizationVerified`. |
| Content safety | Test prompt injection in user input, catalog fields and Memory; secrets/PII requests, oversized requests/results, unsupported writes and tool-loop exhaustion must fail safely. Never use real secrets in fixtures. |
| Reliability and latency | Measure first-token/whole-turn p50/p95, timeout/error rates and scaling under representative concurrent sessions. Exercise Lambda throttling, model throttling, upstream failure, disconnect/cancel and Runtime expiry. Set customer SLO/quotas from results. |
| Memory and recovery | Verify that a conversation outlives its Runtime session, long-term memory on and off, paginated history, deletion verification and namespace isolation. Restore a catalog snapshot into an isolated destination and prove alias rollback. Record RPO/RTO; no untested recovery promise. |
| Operations and privacy | Confirm structured logs, traces, token metrics, alarm delivery and on-call ownership. Verify redaction with content capture off. Explicitly approve storage/retention before online evaluations. |

`scripts/smoke.py --invoke` is an **opt-in billed agent invocation that writes Memory**. It requires `ARIA_API_URL` and a short-lived `ARIA_ACCESS_TOKEN`; supply secrets through the environment without committing or printing them. It creates a session and checks one Korean response, and does not replace the broader acceptance matrix. It performs no cleanup/deletion automatically.

Keep online Evaluations off until telemetry and privacy prerequisites are met. At minimum complete a recorded representative quality/safety regression set before launch; if online scoring is enabled, start with a low approved sample and monitor cost/coverage. No live invocation, login, load or recovery evidence is produced by local unit tests.

## Host the web app

The storefront is a static SPA; no stack hosts it.

1. Build it with `pnpm --filter nordly-web generate`, which writes `packages/web/dist/`. Public configuration (`API_URL`, `OIDC_*`, `BRAND_NAME`; see [FRONTEND.md](../FRONTEND.md)) is fixed at build time, so rebuild when it changes.
2. Serve it over HTTPS from an origin listed in `auth.callbackUrls`, `auth.logoutUrls` and `api.corsAllowOrigins`, and rewrite unknown paths, including `/auth/callback`, to `index.html`.
3. Send a Content-Security-Policy header from the host ([SECURITY.md](../SECURITY.md)).

## Container build and runtime layout

Two steps need a container runtime:

1. **Release image build** — `scripts/build_images.py` builds the `agent`,
   `api` and `indexer` images (`linux/arm64`) and saves them as tarballs under `artifacts/`.
2. **CDK image assets** — `scripts/plan_deployment.sh` runs `cdk synth`, which
   stages the build contexts of the agent and API images, and of the indexer's with `opensearch` (`DockerImageAsset`), and `scripts/deploy.sh` builds the images when it publishes the assets. Both scripts stop before they start if no runtime is found. The Lambda assets are copied locally and need no container.

You can supply that runtime three ways: **Docker**, **Finch**, or **CodeBuild**.

### Docker (default)

If `docker` is on `PATH`, everything works with no configuration.
`build_images.py` uses it (`CONTAINER_ENGINE`, default `docker`) and
`plan_deployment.sh` and `deploy.sh` detect it for `CDK_DOCKER`.

### Finch

[Finch](https://runfinch.com/) is a Docker-compatible CLI. Start its VM once
(`finch vm start`), then:

- **Image build:** `CONTAINER_ENGINE=finch python3 scripts/build_images.py`
- **CDK image assets:** `plan_deployment.sh` and `deploy.sh` prefer Docker but fall back to Finch
  automatically; force it with `CDK_DOCKER=finch`. CDK shells out to the value of
  `CDK_DOCKER` as a Docker-compatible CLI.

### CodeBuild

To build in AWS rather than on a developer machine or a CI runner, point an
AWS CodeBuild project at the repository's [`buildspec.yml`](../../buildspec.yml). It
runs the same `scripts/build_images.py` and emits `artifacts/{agent,api,indexer}.tar`
plus `artifacts/release.json` as build artifacts (it does not push; scan or
publish the tarballs in a downstream stage).

The project must be configured with:

- **`privilegedMode: true`** — the image build needs a Docker daemon.
- **an ARM64 environment image** — the images are `linux/arm64`, e.g.
  `aws/codebuild/amazonlinux-aarch64-standard:3.0`.

`build_images.py` needs only `python3`, `git`, and a Docker daemon (all present
in that image), so the buildspec adds no `uv`/`pnpm` install. CDK synth/bundling
still runs from `plan_deployment.sh` on a Docker/Finch host (or a second
CodeBuild project with the same privileged ARM64 setup).

### Choosing

Docker and Finch are interchangeable and selected per machine — there is no
per-deployment config field, only the `CONTAINER_ENGINE` / `CDK_DOCKER` env
vars. Use Finch on hosts without Docker Desktop; use CodeBuild when a build
should run in AWS instead of on a developer or CI runner.

### Direct image builds

The agent and API Dockerfiles copy the shared contracts package, and the agent and indexer Dockerfiles the catalog package. Run from the repository root with `packages` as the context:

```sh
finch build -f packages/agent/Dockerfile -t aria-agent:local packages
finch build -f packages/api/Dockerfile -t aria-api:local packages
finch build -f packages/indexer/Dockerfile -t aria-indexer:local packages
```

Replace `finch` with `docker` if selected. The agent image is ARM64 and serves `:8080`; the API defaults to `:8000`. Both start the installed environment through `opentelemetry-instrument` without installing dependencies at startup. The indexer image, also ARM64, runs `python -m aria_indexer` once and exits; `--help` lists its arguments. Building from inside an individual package excludes `contracts` and `catalog` and does not match these Dockerfiles.
