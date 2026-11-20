"""Bedrock guardrail applied to the agent's model invocations."""

import hashlib
import json

import aws_cdk as cdk
from aws_cdk import aws_bedrock as bedrock
from constructs import Construct

# Credentials and payment secrets a shopper must never type into, or read from, the chat.
BLOCKED_PII = (
    "CREDIT_DEBIT_CARD_NUMBER",
    "CREDIT_DEBIT_CARD_CVV",
    "CREDIT_DEBIT_CARD_EXPIRY",
    "PIN",
    "PASSWORD",
    "AWS_ACCESS_KEY",
    "AWS_SECRET_KEY",
)
# Korea's resident registration number, YYMMDD-GNNNNNN. The hyphen and the date-shaped birth date
# keep it off the bare 13-digit numbers that screened tool results carry, such as EAN-13 barcodes
# (8801234567890) and epoch-millisecond timestamps (1763481234567). Bedrock evaluates the pattern
# itself; the tests check it with Python's re, which reads these constructs the same way.
RESIDENT_REGISTRATION_NUMBER = r"\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])-[1-4]\d{6}"


class Guardrail(Construct):
    def __init__(self, scope: Construct, construct_id: str, *, mode: str = "ENFORCE"):
        super().__init__(scope, construct_id)

        # ENFORCE blocks matched content; AUDIT evaluates and reports in the
        # trace without blocking (filter action NONE).
        self.mode = mode
        ns: str = self.node.try_get_context("ns")

        properties = {
            "name": f"{ns}Guardrail",
            "blocked_input_messaging": "죄송합니다. 허용되지 않은 입력 메시지입니다.",
            "blocked_outputs_messaging": "죄송합니다. 해당 요청에 대해서는 응답할 수 없습니다.",
            "cross_region_config": {"guardrail_profile_arn": self._guardrail_profile_arn()},
            "content_policy_config": {
                "content_filters_tier_config": {"tier_name": "STANDARD"},
                "filters_config": [
                    self._filter("SEXUAL", "MEDIUM"),
                    self._filter("VIOLENCE", "HIGH"),
                    self._filter("HATE", "HIGH"),
                    self._filter("INSULTS", "HIGH"),
                    self._filter("MISCONDUCT", "MEDIUM"),
                    self._filter("PROMPT_ATTACK", "HIGH"),
                ],
            },
            # Blocked, not masked: masking (ANONYMIZE) is unsupported with async stream processing.
            "sensitive_information_policy_config": {
                "pii_entities_config": [self._sensitive({"type": kind}) for kind in BLOCKED_PII],
                "regexes_config": [self._sensitive({
                    "name": "KR_RESIDENT_REGISTRATION_NUMBER",
                    "description": "Korean resident registration number",
                    "pattern": RESIDENT_REGISTRATION_NUMBER,
                })],
            },
        }
        fingerprint = hashlib.sha256(
            json.dumps(properties, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:16]
        self.guardrail = bedrock.CfnGuardrail(
            self, "Guardrail",
            **{
                **properties,
                "cross_region_config": bedrock.CfnGuardrail.GuardrailCrossRegionConfigProperty(
                    **properties["cross_region_config"]
                ),
                "content_policy_config": bedrock.CfnGuardrail.ContentPolicyConfigProperty(
                    content_filters_tier_config=bedrock.CfnGuardrail.ContentFiltersTierConfigProperty(
                        **properties["content_policy_config"]["content_filters_tier_config"]
                    ),
                    filters_config=[
                        bedrock.CfnGuardrail.ContentFilterConfigProperty(**config)
                        for config in properties["content_policy_config"]["filters_config"]
                    ],
                ),
                "sensitive_information_policy_config": bedrock.CfnGuardrail.SensitiveInformationPolicyConfigProperty(
                    pii_entities_config=[
                        bedrock.CfnGuardrail.PiiEntityConfigProperty(**config)
                        for config in properties["sensitive_information_policy_config"]["pii_entities_config"]
                    ],
                    regexes_config=[
                        bedrock.CfnGuardrail.RegexConfigProperty(**config)
                        for config in properties["sensitive_information_policy_config"]["regexes_config"]
                    ],
                ),
            },
        )

        self.version = bedrock.CfnGuardrailVersion(
            self,
            "GuardrailVersion",
            guardrail_identifier=self.guardrail.attr_guardrail_id,
            # A draft update does not update a numbered version. Description
            # requires replacement, publishing changed controls for the Runtime.
            description=f"Policy configuration {fingerprint}",
        )
        self.version.apply_removal_policy(cdk.RemovalPolicy.RETAIN)

    @property
    def guardrail_id(self) -> str:
        return self.guardrail.attr_guardrail_id

    @property
    def guardrail_version(self) -> str:
        return self.version.attr_version

    @property
    def apply_resources(self) -> list[str]:
        stack = cdk.Stack.of(self)
        # Cross-region guardrails authorize the selected profile at the
        # destination as well as the configured source guardrail.
        profile = self._guardrail_profile_arn().rsplit("/", 1)[1]
        return [
            self.guardrail.attr_guardrail_arn,
            f"arn:{stack.partition}:bedrock:*:{stack.account}:guardrail-profile/{profile}",
        ]

    def _filter(self, filter_type: str, strength: str) -> dict:
        # AUDIT keeps the filters enabled (so matches surface in the trace) but
        # sets the action to NONE so nothing is blocked. Returned as a dict so
        # it feeds both the version fingerprint hash and CfnGuardrail.
        action = "BLOCK" if self.mode == "ENFORCE" else "NONE"
        return {
            "type": filter_type,
            "input_action": action,
            "input_enabled": True,
            "input_strength": strength,
            "output_action": action,
            "output_enabled": filter_type != "PROMPT_ATTACK",
            "output_strength": "NONE" if filter_type == "PROMPT_ATTACK" else strength,
        }

    def _sensitive(self, entity: dict) -> dict:
        """A sensitive-information entity or regex, checked on input and output.

        ENFORCE blocks a match; AUDIT reports it in the trace with action NONE.
        """
        action = "BLOCK" if self.mode == "ENFORCE" else "NONE"
        return {**entity, "action": action, "input_action": action, "input_enabled": True,
                "output_action": action, "output_enabled": True}

    def _guardrail_profile_arn(self) -> str:
        stack = cdk.Stack.of(self)
        # Only apac and us profiles exist today.
        profile_region = "apac" if stack.region.startswith("ap-") else "us"
        return (
            f"arn:aws:bedrock:{stack.region}:{stack.account}:"
            f"guardrail-profile/{profile_region}.guardrail.v1:0"
        )
