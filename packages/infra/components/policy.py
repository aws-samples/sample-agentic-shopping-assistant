"""AgentCore Policy: Cedar authorization on Gateway tool calls — extension point.

## Adding your own policy

Write a Cedar template under `components/policies/` (any folder but `sample/`) and
name it in the target's `policyFile`. The config loader checks the path, and the
Gateway stack deploys the template as one `CfnPolicy` per allowed tool. Two
placeholders are substituted at synth time so files stay environment-agnostic:

    {{GATEWAY_ARN}}  the gateway the engine is attached to
    {{TOOL}}         "<target-name>___<tool-name>"

`components/policies/sample/` holds the saved-items sample's own policies. They are
loaded here, ordered by filename, and applied to that sample's tool only.

## Cedar essentials

Default-deny: with no matching `permit`, every call is DENIED. `forbid`
always overrides `permit`, so a deny rule cannot be defeated by ordering.

Available in conditions:

    principal.getTag("<claim>")   claims from the caller's OAuth token
    context.input.<param>         the tool call's arguments

## Rollout

`mode = "LOG_ONLY"` evaluates and logs decisions without blocking. Deploy
that first, read the decision logs, and only then switch to `ENFORCE` — an
incorrect policy set in ENFORCE mode removes the agent's tools.
"""

from dataclasses import dataclass
from pathlib import Path

from aws_cdk import aws_bedrockagentcore as agentcore

# The saved-items sample's policies. Customer templates elsewhere in policies/ are never loaded
# from here; each is deployed only for the target whose policyFile names it.
_SAMPLE_POLICY_DIR = Path(__file__).parent / "policies" / "sample"

# Cedar rejects names outside [A-Za-z][A-Za-z0-9_]*, and they are immutable.
_NAME_TRANSLATION = str.maketrans({"-": "_", ".": "_"})


@dataclass(frozen=True)
class PolicySpec:
    construct_id: str
    name: str
    statement: str
    description: str


def _policy_name(stem: str) -> str:
    """Turn "01-allow-shopper-tools" into "p01_allow_shopper_tools"."""
    cleaned = stem.translate(_NAME_TRANSLATION)
    return cleaned if cleaned[0].isalpha() else f"p{cleaned}"


def load_policies(gateway_arn: str, tool_name: str) -> list[PolicySpec]:
    """Read the sample's .cedar files, substituting environment placeholders."""
    specs: list[PolicySpec] = []
    for path in sorted(_SAMPLE_POLICY_DIR.glob("*.cedar")):
        statement = (
            path.read_text(encoding="utf-8")
            .replace("{{GATEWAY_ARN}}", gateway_arn)
            .replace("{{TOOL}}", tool_name)
        )
        if not statement.strip():
            continue

        # First comment line doubles as the human-readable description.
        first_comment = next(
            (
                line.lstrip("/ ").strip()
                for line in statement.splitlines()
                if line.strip().startswith("//") and line.lstrip("/ ").strip()
            ),
            path.stem,
        )
        specs.append(
            PolicySpec(
                construct_id=f"Policy{path.stem.translate(_NAME_TRANSLATION).title().replace('_', '')}",
                name=_policy_name(path.stem),
                statement=statement,
                description=first_comment[:4096],
            )
        )
    return specs


def build_policy(
    scope,
    spec: PolicySpec,
    policy_engine_id: str,
    enforcement_mode: str,
) -> agentcore.CfnPolicy:
    return agentcore.CfnPolicy(
        scope,
        spec.construct_id,
        policy_engine_id=policy_engine_id,
        name=spec.name,
        description=spec.description,
        definition=agentcore.CfnPolicy.PolicyDefinitionProperty(
            cedar=agentcore.CfnPolicy.CedarPolicyProperty(statement=spec.statement)
        ),
        enforcement_mode=enforcement_mode,
        validation_mode="FAIL_ON_ANY_FINDINGS",
    )
