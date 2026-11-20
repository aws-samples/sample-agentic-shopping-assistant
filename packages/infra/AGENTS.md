# packages/infra

The CDK app (`app.py`) and its typed configuration (`config/loader.py`). Every key is listed in the [configuration reference](../../docs/generated/config-reference.md).

## Rules

- Constructs go in `components/`, stacks in `stacks/`. A `constructs/` package would shadow the CDK dependency.
- `ARIA_CONFIG` selects an explicit TOML file; otherwise the gitignored `.toml` is used. Never commit customer configuration, and never replace an active config with a template. Unknown keys and unsafe Prod combinations must fail validation.
- The agent and AgentCore Memory are fixed. The selectable integrations are:
  - Cognito or BYO OIDC;
  - the fixture, OpenSearch or HTTPS catalog, with a catalog definition (`tools.productSearch.catalogFile`);
  - customer Lambda, MCP, OpenAPI or API Gateway targets;
  - an existing Gateway.

  Don't create resources for disabled options.
- Preserve exact JWT issuers, recipient and scope checks, and the Runtime's Authorization header allowlist.
- Prod requires explicit account and Region, HTTPS, capacity, alerting and Cedar `ENFORCE`. Shadow-test in Dev; never allow an unsecured first Prod rollout.
- The only other package the app imports is `aria_catalog.definition`, which is standard library only; the others are image contexts and Lambda assets.
- Scope IAM to named resources. Every cdk-nag exception needs an accurate reason; prefer resource-level suppressions ([exceptions](../../docs/generated/cdk-nag-exceptions.md)).
- Reference another stack's resource by fixed name, not by construct, when it may ever be removed ([deployment safety](../../docs/design-docs/deployment-safety.md)).
- Protect existing data with deliberate import, retention and migration decisions. Don't rename or delete customer resources as cleanup.

## Commands

- `uv run --frozen pytest` runs offline synth and profile checks, with no Docker and no AWS calls.
- Deployment, bootstrap, imports, data writes and deletion are operator actions, never implied by running checks ([release](../../docs/guides/release.md)).

## Read

- [Integrations](../../docs/guides/integrations.md), [Gateway onboarding](../../docs/guides/gateway-onboarding.md)
- [Deployment safety](../../docs/design-docs/deployment-safety.md), [SECURITY.md](../../docs/SECURITY.md)
