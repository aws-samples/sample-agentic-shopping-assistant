import base64
import json
import logging

import pytest

import handler

# Reference values computed with aria_contracts.identity.actor_id, so this stdlib-only
# copy of the derivation cannot drift from the contract.
COGNITO_ISSUER = "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_EXAMPLE"
ALICE = "974dfe115d04f5a70c5c11a331fc916c3f7401d96d6e7f2d1c03918fca872388"
BOB_AT_ACME = "c75b771dfd777e490c7b511be8bc130ff6d07d9cdab7d3004d6464bd8e657f28"
AGENT_CLIENT = "agent-client"


def jwt(**claims) -> str:
    def part(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return f"Bearer {part({'alg': 'RS256'})}.{part(claims)}.signature"


# The agent's trace ID (W3C), and another trace as an X-Ray root and as 32 hex characters.
TRACE = "6740a1b24bf92f3577b34da6a3ce929d"
OTHER_ROOT = "1-6740a1b3-a3ce929d4bf92f3577b34da6"
OTHER_TRACE = "6740a1b3a3ce929d4bf92f3577b34da6"

ON_BEHALF_OF_ALICE = jwt(iss=COGNITO_ISSUER, sub="alice-sub", act={"sub": AGENT_CLIENT}, aud="aria-tools")
AGENT_M2M = jwt(iss=COGNITO_ISSUER, sub=AGENT_CLIENT, client_id=AGENT_CLIENT)


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    monkeypatch.setenv("GATEWAY_AUTH_FLOW", "m2m")
    monkeypatch.setenv("AGENT_CLIENT_ID", AGENT_CLIENT)
    monkeypatch.delenv("FORWARD_HEADERS", raising=False)
    monkeypatch.delenv("OIDC_TENANT_CLAIM", raising=False)


@pytest.fixture
def on_behalf_of(monkeypatch):
    monkeypatch.setenv("GATEWAY_AUTH_FLOW", "on_behalf_of")


TOOL_CALL = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "saved-items___get_saved_items"}}
_UNSET = object()


def make_event(headers=None, body=_UNSET):
    gateway_request = {
        "path": "/mcp",
        "httpMethod": "POST",
        "headers": headers if headers is not None else {},
    }
    if body is not None:
        gateway_request["body"] = TOOL_CALL if body is _UNSET else body
    return {"interceptorInputVersion": "1.0", "mcp": {"gatewayRequest": gateway_request}}


def call(headers, body=_UNSET):
    return handler.lambda_handler(make_event(headers=headers, body=body), None)


def forwarded(result):
    return result["mcp"]["transformedGatewayRequest"]["headers"]


def refused(caplog, headers, body=_UNSET, *, reason, code=handler.ACCESS_DENIED):
    """The call never reaches the target: the Gateway answers with a JSON-RPC error for its id.

    The reason goes only to the log, under the line the denial alarm counts.
    """
    request = TOOL_CALL if body is _UNSET else body
    caplog.clear()
    with caplog.at_level(logging.INFO):
        result = call(headers, body=body)
    assert "transformedGatewayRequest" not in result["mcp"]
    response = result["mcp"]["transformedGatewayResponse"]
    assert response["statusCode"] == 200
    error = response["body"]["error"]
    assert response["body"] == {"jsonrpc": "2.0", "id": request["id"], "error": error}
    assert error["code"] == code and reason not in error["message"]
    assert any(r.getMessage().startswith(f"{handler.DENIAL_LOG}: ") and reason in r.getMessage() for r in caplog.records)
    return response["body"]


class TestInterceptorContract:
    def test_declares_output_version(self):
        result = handler.lambda_handler(make_event(body={"method": "tools/list"}), None)
        # The Gateway rejects responses without this field.
        assert result["interceptorOutputVersion"] == "1.0"

    def test_body_passes_through_unchanged(self):
        result = call({"x-aria-actor-id": ALICE, "Authorization": AGENT_M2M})
        assert result["mcp"]["transformedGatewayRequest"]["body"] == TOOL_CALL

    @pytest.mark.parametrize("event", [{}, make_event(body=None)])
    def test_an_event_without_a_request_body_is_denied(self, event):
        with pytest.raises(PermissionError, match="single JSON-RPC message"):
            handler.lambda_handler(event, None)

    @pytest.mark.parametrize("body", ["raw-string", [TOOL_CALL], [{"method": "tools/list"}, TOOL_CALL], 42])
    def test_non_object_bodies_are_denied(self, body):
        # A JSON-RPC batch would carry tool calls past the per-call identity check.
        with pytest.raises(PermissionError, match="single JSON-RPC message"):
            call({"x-aria-actor-id": ALICE, "Authorization": AGENT_M2M}, body=body)


class TestMethods:
    @pytest.mark.parametrize(
        "method", ["initialize", "ping", "tools/list", "notifications/initialized", "notifications/cancelled"]
    )
    def test_the_agents_methods_pass(self, method):
        result = call({"Authorization": AGENT_M2M}, body={"jsonrpc": "2.0", "method": method})
        assert "x-aria-actor-id" not in forwarded(result)

    @pytest.mark.parametrize(
        "method", ["resources/read", "prompts/get", "sampling/createMessage", "tools/call/x", "", None, 7]
    )
    def test_other_methods_are_denied(self, caplog, method):
        refused(caplog, {"x-aria-actor-id": ALICE, "Authorization": AGENT_M2M},
                body={"jsonrpc": "2.0", "id": 1, "method": method}, reason="is not allowed", code=handler.METHOD_NOT_FOUND)

    def test_a_message_without_a_method_is_denied(self):
        # A response object is no request, so there is nothing to answer: the denial is raised.
        with pytest.raises(PermissionError, match="is not allowed"):
            call({"Authorization": AGENT_M2M}, body={"jsonrpc": "2.0", "id": 1, "result": {}})


class TestSettings:
    @pytest.mark.parametrize("missing", ["GATEWAY_AUTH_FLOW", "AGENT_CLIENT_ID"])
    def test_every_request_is_denied_without_the_settings(self, caplog, monkeypatch, missing):
        monkeypatch.delenv(missing)
        refused(caplog, {"Authorization": AGENT_M2M}, body={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                reason="must both be set")

    def test_a_notification_without_the_settings_is_raised(self, monkeypatch):
        # A notification has no id to answer, so its denial is raised and the Gateway refuses it.
        monkeypatch.delenv("AGENT_CLIENT_ID")
        with pytest.raises(PermissionError, match="must both be set"):
            call({"Authorization": AGENT_M2M}, body={"jsonrpc": "2.0", "method": "notifications/initialized"})

    def test_an_unknown_flow_is_denied(self, caplog, monkeypatch):
        monkeypatch.setenv("GATEWAY_AUTH_FLOW", "user_federation")
        refused(caplog, {"x-aria-actor-id": ALICE, "Authorization": AGENT_M2M}, reason="must both be set")

    @pytest.mark.parametrize("flow,token", [("ON_BEHALF_OF_TOKEN_EXCHANGE", ON_BEHALF_OF_ALICE), ("M2M", AGENT_M2M)])
    def test_the_agents_spelling_of_each_flow_is_accepted(self, monkeypatch, flow, token):
        monkeypatch.setenv("GATEWAY_AUTH_FLOW", flow)
        assert forwarded(call({"x-aria-actor-id": ALICE, "Authorization": token}))["x-aria-actor-id"] == ALICE


class TestActorIdentity:
    def test_actor_reaches_the_target_on_a_tool_call(self):
        result = call({"X-Aria-Actor-Id": ALICE, "Authorization": AGENT_M2M})
        assert forwarded(result)["x-aria-actor-id"] == ALICE

    @pytest.mark.parametrize("value", [None, "", "alice", ALICE.upper(), ALICE + "0", ALICE + "\n"])
    def test_tool_calls_without_a_valid_actor_are_denied(self, caplog, value):
        headers = {"Authorization": AGENT_M2M} | ({} if value is None else {"x-aria-actor-id": value})
        refused(caplog, headers, reason="x-aria-actor-id")

    def test_conflicting_actor_headers_are_denied(self, caplog):
        refused(caplog, {"x-aria-actor-id": ALICE, "X-Aria-Actor-Id": "f" * 64, "Authorization": AGENT_M2M},
                reason="Conflicting x-aria-actor-id")

    def test_listing_tools_needs_no_actor(self):
        result = call({"Authorization": AGENT_M2M}, body={"method": "tools/list"})
        assert "x-aria-actor-id" not in forwarded(result)

    def test_matches_the_contract_derivation(self):
        assert handler.actor_id({"iss": COGNITO_ISSUER, "sub": "alice-sub"}) == ALICE
        assert handler.actor_id({"iss": "https://issuer.example", "sub": "bob", "tenant": "acme"}, "tenant") == BOB_AT_ACME


@pytest.mark.usefixtures("on_behalf_of")
class TestOnBehalfOf:
    def test_the_token_must_name_the_same_shopper(self, caplog):
        result = call({"x-aria-actor-id": ALICE, "Authorization": ON_BEHALF_OF_ALICE})
        assert forwarded(result)["x-aria-actor-id"] == ALICE
        refused(caplog, {"x-aria-actor-id": "f" * 64, "Authorization": ON_BEHALF_OF_ALICE}, reason="does not match")

    def test_tenant_claim_is_part_of_the_check(self, monkeypatch):
        monkeypatch.setenv("OIDC_TENANT_CLAIM", "tenant")
        token = jwt(iss="https://issuer.example", sub="bob", tenant="acme", act={"sub": AGENT_CLIENT})
        result = call({"x-aria-actor-id": BOB_AT_ACME, "Authorization": token})
        assert forwarded(result)["x-aria-actor-id"] == BOB_AT_ACME

    @pytest.mark.parametrize(
        "token",
        [
            # Without `act` the token isn't the agent's on-behalf-of token, so it is denied
            # even though its subject matches the header.
            jwt(iss=COGNITO_ISSUER, sub="alice-sub"),
            AGENT_M2M,
            jwt(iss=COGNITO_ISSUER, sub="alice-sub", act={"sub": "another-client"}),
            jwt(iss=COGNITO_ISSUER, sub="alice-sub", act="agent-client"),
            "",
        ],
    )
    def test_tokens_not_minted_by_the_agent_on_the_shoppers_behalf_are_denied(self, caplog, token):
        headers = {"x-aria-actor-id": ALICE} | ({"Authorization": token} if token else {})
        refused(caplog, headers, reason="on-behalf-of")


class TestClientCredentials:
    def test_the_agents_token_carries_the_agents_header(self):
        # An M2M token names only the agent's client, so its subject is not compared.
        result = call({"x-aria-actor-id": ALICE, "Authorization": AGENT_M2M})
        assert forwarded(result)["x-aria-actor-id"] == ALICE

    @pytest.mark.parametrize(
        "token",
        [
            jwt(iss=COGNITO_ISSUER, sub="other-client", client_id="other-client"),
            jwt(iss=COGNITO_ISSUER, sub=AGENT_CLIENT),  # no client_id claim
            ON_BEHALF_OF_ALICE,
            "",
        ],
    )
    def test_tokens_from_other_clients_are_denied(self, caplog, token):
        headers = {"x-aria-actor-id": ALICE} | ({"Authorization": token} if token else {})
        refused(caplog, headers, reason="agent's client")


class TestRefusals:
    def test_a_string_id_is_echoed(self, caplog):
        body = {**TOOL_CALL, "id": "call-7"}
        assert refused(caplog, {"Authorization": AGENT_M2M}, body=body, reason="x-aria-actor-id")["id"] == "call-7"

    def test_a_boolean_id_is_no_id_to_answer(self):
        with pytest.raises(PermissionError, match="is not allowed"):
            call({"Authorization": AGENT_M2M}, body={"jsonrpc": "2.0", "id": True, "method": "resources/read"})

    def test_an_unexpected_failure_is_raised(self, monkeypatch):
        # Not a refusal the interceptor chose: the Gateway denies the call itself.
        def broken(*_args):
            raise RuntimeError("bug")

        monkeypatch.setattr(handler, "checked_actor", broken)
        with pytest.raises(RuntimeError):
            call({"x-aria-actor-id": ALICE, "Authorization": AGENT_M2M})

    def test_logs_header_names_never_values(self, caplog):
        with caplog.at_level(logging.INFO):
            call({"x-aria-actor-id": ALICE, "Authorization": AGENT_M2M, "X-Locale": "ko-KR"})
        logged = " ".join(r.getMessage() for r in caplog.records)
        assert "authorization" in logged and "x-locale" in logged
        assert AGENT_M2M.split(".")[1] not in logged and "ko-KR" not in logged


class TestHeaderForwarding:
    def call(self, headers, monkeypatch=None, forward=""):
        if monkeypatch is not None:
            monkeypatch.setenv("FORWARD_HEADERS", forward)
        return forwarded(call({"x-aria-actor-id": ALICE, "Authorization": AGENT_M2M, **headers}))

    def test_authorization_is_never_forwarded(self, monkeypatch):
        headers = self.call({}, monkeypatch, forward="authorization,auth")
        assert not {k.lower() for k in headers} & {"authorization", "auth"}

    def test_the_agents_trace_becomes_the_correlation_id(self):
        # The agent's traceparent names its own trace, and wins over the root AWS adds.
        headers = self.call({"traceparent": f"00-{TRACE}-00f067aa0ba902b7-01", "X-Amzn-Trace-Id": f"Root={OTHER_ROOT}"})
        assert headers["x-correlation-id"] == TRACE

    def test_without_a_traceparent_the_xray_root_is_the_correlation_id(self):
        assert self.call({"X-Amzn-Trace-Id": f"Self=1-6740a1b3-0123456789abcdef01234567;Root={OTHER_ROOT};Sampled=1"})[
            "x-correlation-id"] == OTHER_TRACE

    @pytest.mark.parametrize("trace", [{}, {"traceparent": "00-" + "0" * 32 + "-00f067aa0ba902b7-01"},
                                       {"traceparent": "not a trace"}, {"x-amzn-trace-id": "Root=1-bad"}])
    def test_no_usable_trace_means_no_correlation_id(self, trace):
        assert "x-correlation-id" not in self.call(trace)

    def test_custom_allowlist_cannot_replace_the_correlation_id(self, monkeypatch):
        headers = self.call({"traceparent": f"00-{TRACE}-00f067aa0ba902b7-01", "X-Correlation-Id": "forged"},
                            monkeypatch, forward="x-correlation-id")
        assert headers["x-correlation-id"] == TRACE

    def test_forwards_allowlisted_custom_headers(self, monkeypatch):
        headers = self.call({"x-locale": "ko-KR", "x-tenant-id": "acme"}, monkeypatch, forward="x-locale,x-tenant-id")
        assert headers["x-locale"] == "ko-KR" and headers["x-tenant-id"] == "acme"

    def test_match_is_case_insensitive_and_preserves_casing(self, monkeypatch):
        assert self.call({"X-Locale": "ko-KR"}, monkeypatch, forward="x-locale")["X-Locale"] == "ko-KR"

    def test_drops_headers_not_in_allowlist(self, monkeypatch):
        headers = self.call({"x-locale": "ko-KR", "x-secret": "s"}, monkeypatch, forward="x-locale")
        assert "x-secret" not in headers

    def test_custom_allowlist_cannot_replace_the_checked_actor(self, monkeypatch):
        headers = self.call({}, monkeypatch, forward="x-aria-actor-id")
        assert headers["x-aria-actor-id"] == ALICE
