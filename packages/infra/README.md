# ARIA infrastructure

[한국어](README.ko.md)

Python CDK deploys a fixed AgentCore Runtime and AgentCore Memory, whose long-term extraction is opt-in. Cognito, tool targets and the OpenSearch catalog (the domain, its Nori plugin, a private catalog bucket and the indexing task) are conditional on typed integration configuration. The API runs on Fargate behind ALB/WAF; an Operations stack supplies alarms and a dashboard. Continuous Evaluations is opt-in.

Use `ARIA_CONFIG=/absolute/path/config.toml`; otherwise the loader reads gitignored `.toml`. `config/dev.toml` is a Dev template that uses the packaged example catalog through the fixture provider. `tools.productSearch.catalogFile` names your catalog definition, relative to the config file; the loader validates it at synth and passes it to the Runtime and the indexing task as `CATALOG_DEFINITION`. `config/prod.toml` is a production template with placeholders that must be replaced, its HTTP catalog endpoint among them. Production requires explicit account/region, HTTPS/certificate/CORS, at least two API tasks, enforced tool authorization and a notification destination, and rejects the fixture catalog. Managed VPCs use one NAT per AZ in Prod; Dev defaults to one NAT and three interface endpoint services. Existing VPCs retain customer-owned routing.

Run `uv sync --frozen && uv run --frozen pytest`. `tests/test_profiles.py` constructs the real application with Docker bundling and AWS lookups disabled, attaches AwsSolutionsChecks, and fails on nag errors across sample/BYO and Dev/Prod profiles. These are synthesis checks, not deployment or recovery evidence.

Before any deployment, read [release](../../docs/guides/release.md), [integration](../../docs/guides/integrations.md) and [operations](../../docs/guides/operations.md). Never replace an active configuration with `config/dev.toml` or `config/prod.toml`: `dev.toml` selects the example catalog, `prod.toml` a placeholder HTTP catalog, and both leave long-term memory off. External resources remain customer-owned.
