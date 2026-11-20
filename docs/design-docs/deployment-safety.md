# Deployment safety

Status: verified

How ARIA keeps a deployment exactly what was reviewed. The procedure is in [release](../guides/release.md).

## The reviewed plan is the deployment

- `scripts/plan_deployment.sh` refuses a dirty checkout. It runs read-only preflight, synthesizes into an empty `artifacts/cdk.out`, and writes a CDK diff for review.
- The plan records the commit, the configuration file's hash and the hash of every assembly file in `artifacts/deployment-plan.json`.
- `scripts/deploy.sh` needs `ARIA_ALLOW_DEPLOY=yes`. It refuses to deploy if the commit, the configuration or any assembly file differs from the plan, and deploys that assembly without resynthesis.
- Both scripts run CDK with `--lookups false`, so a plan never queries the account. Account lookups, such as availability zones, are recorded once in `packages/infra/cdk.context.json`.

## What a review checks

- Every IAM change, and every replacement.
- **No public endpoints:** no Lambda function URL with `AuthType: NONE`, no Lambda permission for `Principal: "*"`, no ingress from `0.0.0.0/0` or `::/0`, no API route without authorization. This rule has no exceptions without explicit approval.
- No resource deletion that wasn't intended. The diff names each destroyed resource.

## Cross-stack exports fix deploy order

A construct reference across stacks becomes a CloudFormation export, and CloudFormation won't delete an export another stack imports. So removing a resource that another stack references needs two deploys: first the importing stack stops importing, then the owner removes the resource.

- `ARIA_DEPLOY_STACKS=<stack names>` deploys only those stacks from the same verified assembly, for exactly this ordering.
- Prefer referencing by a fixed name over a construct reference. The Operations alarms name the Gateway's functions for this reason, and a test checks that Operations imports nothing from the Gateway.

## Live checks after every deploy

Synthesis can't show whether AgentCore accepts a configuration or whether a role's permissions are enough. Two cases synthesize cleanly but fail on the first live call when they're missing:

- Cognito rejects AgentCore Identity's default subject token type, `…:jwt`, so the agent sends the shopper's token as `…:access_token` ([Cognito notes](../references/cognito-token-exchange-notes.md#the-request)).
- To fetch a target's OAuth token or API key, the Gateway role needs grants on the Gateway's own workload identity as well as on the provider. The sample policy in the AgentCore guide grants only the provider, so the call is denied ([Gateway notes](../references/agentcore-gateway-notes.md#outbound-credentials)).

Failures like these show up only on a live call. After a deploy, run the checks in [testing](../guides/testing.md) against the stage.
