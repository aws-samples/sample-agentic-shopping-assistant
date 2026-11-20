import re

import aws_cdk as cdk
from aws_cdk.assertions import Template

import components.guardrail
from components.guardrail import Guardrail


def rendered_guardrail(mode: str = "ENFORCE"):
    app = cdk.App(context={"ns": "ARIADev"})
    stack = cdk.Stack(app, "Test", env=cdk.Environment(account="123456789012", region="ap-northeast-2"))
    Guardrail(stack, "Safety", mode=mode)
    resources = Template.from_stack(stack).to_json()["Resources"]
    return {
        resource["Type"]: resource
        for resource in resources.values()
    }


def test_numbered_version_changes_with_policy_and_preserves_rollback(monkeypatch):
    original = rendered_guardrail()
    version = original["AWS::Bedrock::GuardrailVersion"]
    assert version["Properties"]["Description"] == rendered_guardrail()["AWS::Bedrock::GuardrailVersion"]["Properties"]["Description"]
    assert version["UpdateReplacePolicy"] == "Retain"
    assert version["DeletionPolicy"] == "Retain"

    original_filter = Guardrail._filter
    # _filter is an instance method (it reads self.mode), so the replacement
    # takes self and forwards it.
    monkeypatch.setattr(Guardrail, "_filter",
        lambda self, kind, strength: original_filter(self, kind, "LOW" if kind == "VIOLENCE" else strength)
    )
    changed = rendered_guardrail()["AWS::Bedrock::GuardrailVersion"]
    assert changed["Properties"]["Description"] != version["Properties"]["Description"]


def test_apply_permissions_cover_only_the_source_and_selected_profile():
    app = cdk.App(context={"ns": "ARIADev"})
    stack = cdk.Stack(app, "Permissions", env=cdk.Environment(account="123456789012", region="ap-northeast-2"))
    guardrail = Guardrail(stack, "Safety")
    resources = stack.resolve(guardrail.apply_resources)
    assert len(resources) == 2
    assert resources[0] == {"Fn::GetAtt": [stack.get_logical_id(guardrail.guardrail), "GuardrailArn"]}
    assert resources[1] == stack.resolve(
        f"arn:{stack.partition}:bedrock:*:123456789012:guardrail-profile/apac.guardrail.v1:0"
    )


def test_content_filters_cover_hate_and_misconduct():
    filters = {f["Type"]: f for f in rendered_guardrail()["AWS::Bedrock::Guardrail"]["Properties"]["ContentPolicyConfig"]["FiltersConfig"]}
    assert set(filters) == {"SEXUAL", "VIOLENCE", "HATE", "INSULTS", "MISCONDUCT", "PROMPT_ATTACK"}
    assert (filters["HATE"]["InputStrength"], filters["HATE"]["OutputStrength"]) == ("HIGH", "HIGH")
    assert (filters["MISCONDUCT"]["InputStrength"], filters["MISCONDUCT"]["OutputStrength"]) == ("MEDIUM", "MEDIUM")
    assert {(f["InputAction"], f["OutputAction"]) for f in filters.values()} == {("BLOCK", "BLOCK")}


def test_credentials_payment_data_and_resident_numbers_are_blocked_both_ways():
    policy = rendered_guardrail()["AWS::Bedrock::Guardrail"]["Properties"]["SensitiveInformationPolicyConfig"]
    entities = {entity["Type"]: entity for entity in policy["PiiEntitiesConfig"]}
    assert set(entities) == {"CREDIT_DEBIT_CARD_NUMBER", "CREDIT_DEBIT_CARD_CVV", "CREDIT_DEBIT_CARD_EXPIRY", "PIN",
                             "PASSWORD", "AWS_ACCESS_KEY", "AWS_SECRET_KEY"}
    (regex,) = policy["RegexesConfig"]
    assert regex["Pattern"] == r"\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])-[1-4]\d{6}"
    for rule in [*entities.values(), regex]:
        # Blocked, never masked: masking isn't supported with async stream processing.
        assert (rule["Action"], rule["InputAction"], rule["OutputAction"]) == ("BLOCK", "BLOCK", "BLOCK")
        assert rule["InputEnabled"] is True and rule["OutputEnabled"] is True


def test_resident_number_pattern_spares_barcodes_and_timestamps():
    # Bedrock evaluates the pattern itself; Python's re reads these constructs the same way. Tool
    # results are screened too, so a bare 13-digit barcode or epoch-millisecond timestamp in an
    # order or product must not be blocked as a resident registration number.
    (regex,) = rendered_guardrail()["AWS::Bedrock::Guardrail"]["Properties"]["SensitiveInformationPolicyConfig"]["RegexesConfig"]
    pattern = re.compile(regex["Pattern"])
    assert pattern.search("900101-1234567")
    assert pattern.search("주민등록번호는 851231-2345678 입니다")
    for harmless in ("8801234567890", "1763481234567", "order 8801234567890 shipped at 1763481234567",
                     "900101-5234567", "901301-1234567", "900132-1234567"):
        assert not pattern.search(harmless), harmless


def test_audit_reports_sensitive_information_without_blocking():
    policy = rendered_guardrail("AUDIT")["AWS::Bedrock::Guardrail"]["Properties"]["SensitiveInformationPolicyConfig"]
    rules = [*policy["PiiEntitiesConfig"], *policy["RegexesConfig"]]
    assert {(r["Action"], r["InputAction"], r["OutputAction"]) for r in rules} == {("NONE", "NONE", "NONE")}
    assert all(r["InputEnabled"] and r["OutputEnabled"] for r in rules)


def test_numbered_version_changes_with_the_sensitive_information_policy(monkeypatch):
    original = rendered_guardrail()["AWS::Bedrock::GuardrailVersion"]["Properties"]["Description"]
    monkeypatch.setattr(components.guardrail, "BLOCKED_PII", components.guardrail.BLOCKED_PII[:-1])
    assert rendered_guardrail()["AWS::Bedrock::GuardrailVersion"]["Properties"]["Description"] != original


def test_no_denied_topics_or_word_lists():
    # Tool results are screened as INPUT, so a denied topic or word list would also block ordinary
    # order or store data from a Gateway tool. Adding one needs that traffic tested first.
    properties = rendered_guardrail()["AWS::Bedrock::Guardrail"]["Properties"]
    assert not {"TopicPolicyConfig", "WordPolicyConfig", "ContextualGroundingPolicyConfig"} & set(properties)
