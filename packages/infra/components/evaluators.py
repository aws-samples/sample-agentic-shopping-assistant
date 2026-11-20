"""Custom evaluators for AgentCore Evaluations — the extension point.

## Adding your own evaluator

Append a `CustomEvaluatorSpec` to `custom_evaluators()`. It is deployed as a
`CfnEvaluator` and attached to the online evaluation config automatically; no
other file needs editing.

    CustomEvaluatorSpec(
        construct_id="MyEvaluator",           # unique within the stack
        name="my_evaluator",                  # letters/digits/underscores
        description="What this measures.",
        level="TRACE",                        # TRACE | SESSION | TOOL_CALL
        instructions=\"\"\"...judge prompt...\"\"\",
        scale=[(1, "bad", "..."), (5, "good", "...")],
    )

`level` decides which placeholders the judge prompt may reference:

| level       | placeholders                                     |
| ----------- | ------------------------------------------------ |
| `TRACE`     | `{context}`, `{assistant_turn}`                  |
| `SESSION`   | `{context}`, `{available_tools}`                 |
| `TOOL_CALL` | `{context}`, `{available_tools}`, `{tool_turn}`  |

Placeholders take single braces; `{{context}}` is rejected by CreateEvaluator.
Do not add output-format instructions: the service appends its own prompt that
asks the judge for a reason and a score.

To use only AWS built-ins, set `includeExampleCustomEvaluator = false` in
`.toml` and return an empty list here.

Built-in evaluator IDs are listed in `.toml` under `[evaluations]`. See the full
catalogue with:

    aws bedrock-agentcore-control list-evaluators --region <region>
"""

from dataclasses import dataclass, field
from typing import Literal

from aws_cdk import aws_bedrockagentcore as agentcore

EvaluatorLevel = Literal["TRACE", "SESSION", "TOOL_CALL"]

# (value, label, definition) — the judge picks one rung and explains why.
ScaleRung = tuple[int, str, str]


@dataclass(frozen=True)
class CustomEvaluatorSpec:
    construct_id: str
    name: str
    description: str
    level: EvaluatorLevel
    instructions: str
    scale: list[ScaleRung] = field(default_factory=list)


# ── Example: catalogue grounding ─────────────────────────────────────
#
# Built-in Faithfulness asks the general question "is the response supported by
# context". This asks the retail-specific one: did the assistant recommend only
# products that actually came back from search, with the attributes the shopper
# asked for? Inventing a plausible product name is the most damaging failure
# mode for a shopping assistant, and it reads as a perfectly fluent answer.
_CATALOGUE_GROUNDING = CustomEvaluatorSpec(
    construct_id="CatalogueGroundingEvaluator",
    name="catalogue_grounding",
    description=(
        "Retail grounding metric. Checks that every product the assistant names "
        "appears in the tool results and matches the shopper's stated constraints."
    ),
    level="TRACE",
    instructions="""
You are grading a retail shopping assistant.

# Conversation and tool results
{context}

# Assistant response being graded
{assistant_turn}

# Task
Decide whether every product the assistant names is genuinely supported by the
tool results in the context above.

Penalise:
- Naming a product that does not appear in any tool result (invented product).
- Describing an attribute (colour, season, gender, price) that contradicts the
  tool result for that product.
- Presenting results as if they matched a constraint the shopper gave when they
  did not, e.g. offering summer items for a winter request.

Do not penalise:
- Saying no matching products were found, when the tool results are genuinely
  empty or errored. That is correct behaviour, not a grounding failure.
- Summarising or paraphrasing a product's name naturally, so long as it is
  clearly the same item.
- General shopping advice that names no specific product.

In your reasoning, name the products you checked against the tool results.
""".strip(),
    scale=[
        (
            1,
            "fabricated",
            "Names at least one product absent from the tool results, or contradicts a product's attributes.",
        ),
        (
            3,
            "partially_grounded",
            "All products exist in the tool results, but some stated attribute or constraint match is inaccurate.",
        ),
        (
            5,
            "fully_grounded",
            "Every named product and attribute is supported by the tool results, or correctly reports no matches.",
        ),
    ],
)


def custom_evaluators() -> list[CustomEvaluatorSpec]:
    """Return the custom evaluators to deploy. Add your own here."""
    return [_CATALOGUE_GROUNDING]


def build_evaluator(
    scope,
    spec: CustomEvaluatorSpec,
    judge_model_id: str,
) -> agentcore.CfnEvaluator:
    """Render a spec into an LLM-as-a-judge CfnEvaluator."""
    return agentcore.CfnEvaluator(
        scope,
        spec.construct_id,
        evaluator_name=spec.name,
        description=spec.description,
        level=spec.level,
        evaluator_config=agentcore.CfnEvaluator.EvaluatorConfigProperty(
            llm_as_a_judge=agentcore.CfnEvaluator.LlmAsAJudgeEvaluatorConfigProperty(
                instructions=spec.instructions,
                model_config=agentcore.CfnEvaluator.EvaluatorModelConfigProperty(
                    bedrock_evaluator_model_config=agentcore.CfnEvaluator.BedrockEvaluatorModelConfigProperty(
                        model_id=judge_model_id,
                        inference_config=agentcore.CfnEvaluator.InferenceConfigurationProperty(
                            # Grading should be repeatable, so pin temperature low.
                            temperature=0.0,
                            max_tokens=1024,
                        ),
                    )
                ),
                rating_scale=agentcore.CfnEvaluator.RatingScaleProperty(
                    numerical=[
                        agentcore.CfnEvaluator.NumericalScaleDefinitionProperty(
                            value=value, label=label, definition=definition
                        )
                        for value, label, definition in spec.scale
                    ]
                ),
            )
        ),
    )
