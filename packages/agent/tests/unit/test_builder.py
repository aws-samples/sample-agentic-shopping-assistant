import httpx
import base64
import json

import pytest
from strands import ModelRetryStrategy

from aria_contracts.identity import actor_id

from core.builder import GUARDRAIL_REFUSAL, SessionBuilder, resolve_actor_id
from core.config import AgentConfig
from core.downstream import DownstreamToken
from core.memory import FACTS_NAMESPACE, PREFERENCES_NAMESPACE, SUMMARIES_NAMESPACE
from core.safety import GuardrailScreen, ToolSafety
from core.transport import gateway_transport
from fake_gateway import TOOL, FakeGateway

SESSION_ID = "session-" + "0" * 25
# These tests build real boto3 clients; the AWS guard in tests/conftest.py fails any request one sends.


class Log:
    def __init__(self):
        self.events = []

    def __getattr__(self, level):
        return lambda event, **fields: self.events.append((level, event, fields))


def make_token(claims: dict) -> str:
    claims = {"iss": "https://issuer.example", **claims}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"Bearer header.{payload}.signature"


def make_config(**overrides) -> AgentConfig:
    defaults = dict(
        gateway_url="",
        model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        region="us-west-2",
        max_tokens=2048,
        temperature=0.3,
        memory_id="",
        memory_strategies=("user_preference", "semantic", "summarization"),
        memory_top_k=5,
        memory_relevance_score=0.3,
        guardrail_id=None,
        guardrail_version=None,
        allowed_tools=("search_products",),
    )
    return AgentConfig(**{**defaults, **overrides})


@pytest.fixture(autouse=True)
def issuer(monkeypatch):
    monkeypatch.setenv("OIDC_ISSUER", "https://issuer.example")


def actor(sub):
    return actor_id({"iss": "https://issuer.example", "sub": sub})


class TestResolveActorId:
    def test_extracts_sub_claim(self):
        assert resolve_actor_id(make_token({"sub": "user-123"})) == actor("user-123")

    def test_handles_missing_bearer_prefix(self):
        token = make_token({"sub": "user-456"}).removeprefix("Bearer ")
        assert resolve_actor_id(token) == actor("user-456")

    @pytest.mark.parametrize("token", ["", "garbage", "a.b.c", "Bearer only-one-part"])
    def test_rejects_bad_token(self, token):
        # Never invent an actor for a token without a verified principal.
        with pytest.raises(ValueError):
            resolve_actor_id(token)

    def test_rejects_missing_subject(self):
        with pytest.raises(ValueError):
            resolve_actor_id(make_token({"email": "a@b.c"}))

    def test_distinct_users_get_distinct_actor_ids(self):
        assert resolve_actor_id(make_token({"sub": "a"})) != resolve_actor_id(make_token({"sub": "b"}))


class TestSessionBuild:
    def test_sets_user_id_in_agent_state(self):
        session = SessionBuilder(make_config()).build(make_token({"sub": "user-789"}), SESSION_ID)
        assert session.agent.state.get("user_id") == actor("user-789")

    def test_no_mcp_client_when_gateway_url_absent(self):
        # Product search is local, so a session without a Gateway still has it.
        session = SessionBuilder(make_config()).build(make_token({"sub": "u"}), SESSION_ID)
        assert session.agent.tool_names == ["search_products"]

    def test_search_can_be_disabled(self):
        session = SessionBuilder(make_config(search_provider="disabled")).build(make_token({"sub": "u"}), SESSION_ID)
        assert session.agent.tool_names == []

    def test_the_search_tool_takes_its_schema_from_the_catalog_definition(self, monkeypatch):
        import aria_catalog

        definition = aria_catalog.definition_to_dict(aria_catalog.load_definition())
        definition["catalog"]["fields"].append({"name": "price", "type": "integer", "filter": "range"})
        monkeypatch.setenv("CATALOG_DEFINITION", json.dumps(definition))

        session = SessionBuilder(make_config()).build(make_token({"sub": "u"}), SESSION_ID)

        [spec] = session.agent.tool_registry.get_all_tool_specs()
        assert spec["name"] == "search_products"
        assert list(spec["inputSchema"]["json"]["properties"])[-2:] == ["price", "limit"]

    def test_the_search_tool_defaults_to_the_example_catalog(self, monkeypatch):
        import aria_catalog

        monkeypatch.delenv("CATALOG_DEFINITION", raising=False)
        session = SessionBuilder(make_config()).build(make_token({"sub": "u"}), SESSION_ID)
        [spec] = session.agent.tool_registry.get_all_tool_specs()
        assert spec == aria_catalog.tool_spec(aria_catalog.load_definition())

    def test_gateway_is_skipped_when_no_gateway_tools_are_allowed(self):
        # Only "<target>___<tool>" names live behind the Gateway; local tools don't need it.
        builder = SessionBuilder(make_config(gateway_url="https://gw/mcp", allowed_tools=("search_products",)))
        assert builder._build_downstream_token() is None
        assert builder._build_mcp_client(None, "a" * 64, {}) is None

    def test_gateway_tools_without_a_gateway_url_run_local_tools_only(self, monkeypatch):
        log = Log()
        monkeypatch.setattr("core.builder.logger", log)
        builder = SessionBuilder(make_config(allowed_tools=("search_products", TOOL)))
        assert builder._build_downstream_token() is None
        assert builder._build_mcp_client(None, "a" * 64, {}) is None
        assert log.events[0][:2] == ("warning", "⚠️ no gateway url configured, running without MCP tools")

    def test_system_prompt_has_no_unformatted_placeholder(self):
        # Preferences arrive via memory retrieval, not prompt interpolation.
        session = SessionBuilder(make_config()).build(make_token({"sub": "u"}), SESSION_ID)
        assert "{" not in session.agent.system_prompt
        assert "NORDLY" in session.agent.system_prompt

    def test_no_memory_configured_yields_no_session_manager(self):
        # Every deployment configures Memory; only a container without AGENTCORE_MEMORY_ID (tests) has none.
        builder = SessionBuilder(make_config(memory_id=""))
        assert builder._build_memory("actor", SESSION_ID) is None

    def test_memory_manager_configured_with_actor_and_namespaces(self, monkeypatch):
        # The real manager calls ListEvents in its constructor to rehydrate
        # history, so intercept it and inspect the config it was handed.
        captured = {}

        class FakeManager:
            def __init__(self, agentcore_memory_config, region_name):
                captured["config"] = agentcore_memory_config
                captured["region"] = region_name

        monkeypatch.setattr(
            "bedrock_agentcore.memory.integrations.strands.session_manager.AgentCoreMemorySessionManager",
            FakeManager,
        )

        builder = SessionBuilder(make_config(memory_id="mem-abcdefghij"))
        manager = builder._build_memory("actor-1", SESSION_ID)

        assert manager is not None
        assert captured["region"] == "us-west-2"
        config = captured["config"]
        assert config.memory_id == "mem-abcdefghij"
        assert config.actor_id == "actor-1"
        assert config.session_id == SESSION_ID
        # batch_size must stay 1: containers are hard-killed without SIGTERM.
        assert config.batch_size == 1
        assert set(config.retrieval_config) == {
            PREFERENCES_NAMESPACE,
            FACTS_NAMESPACE,
            SUMMARIES_NAMESPACE,
        }

    def _capture_memory_config(self, monkeypatch, **overrides):
        captured = {}

        class FakeManager:
            def __init__(self, agentcore_memory_config, region_name):
                captured["config"] = agentcore_memory_config

        monkeypatch.setattr(
            "bedrock_agentcore.memory.integrations.strands.session_manager.AgentCoreMemorySessionManager",
            FakeManager,
        )
        builder = SessionBuilder(make_config(memory_id="mem-abcdefghij", **overrides))
        assert builder._build_memory("actor-1", SESSION_ID) is not None
        return captured["config"]

    def test_short_term_only_retrieves_nothing(self, monkeypatch):
        # No strategies = short-term event store only; events still persist but
        # nothing is injected back into the turn.
        config = self._capture_memory_config(monkeypatch, memory_strategies=())
        assert config.retrieval_config == {}

    def test_retrieval_follows_selected_strategies(self, monkeypatch):
        config = self._capture_memory_config(monkeypatch, memory_strategies=("semantic",))
        assert set(config.retrieval_config) == {FACTS_NAMESPACE}

    def test_namespaces_resolve_with_actor_and_session(self):
        assert PREFERENCES_NAMESPACE.format(actorId="u1", sessionId="s1") == "/users/u1/preferences"
        assert SUMMARIES_NAMESPACE.format(actorId="u1", sessionId="s1") == "/summaries/u1/s1"


class TestLocalTools:
    def test_a_local_tool_missing_from_the_allowlist_is_not_offered(self, monkeypatch):
        # ToolSafety would refuse it at call time, so the model must not see it either.
        log = Log()
        monkeypatch.setattr("core.builder.logger", log)
        session = SessionBuilder(make_config(allowed_tools=())).build(make_token({"sub": "u"}), SESSION_ID)
        assert session.agent.tool_names == []
        assert ("warning", "local_tool_not_allowed", {"tool": "search_products"}) in log.events

    def test_allowed_local_tools_are_offered(self, monkeypatch):
        log = Log()
        monkeypatch.setattr("core.builder.logger", log)
        builder = SessionBuilder(make_config(allowed_tools=("search_products",)))
        session = builder.build(make_token({"sub": "u"}), SESSION_ID)
        assert session.agent.tool_names == ["search_products"]
        assert not [event for event in log.events if event[1] == "local_tool_not_allowed"]


GATEWAY = dict(  # nosec B106 # the Gateway token's audience, a public resource-server name
    gateway_url="https://gw/mcp",
    allowed_tools=("search_products", "saved-items___get_saved_items"),
    gateway_credential_provider="ariadev-gateway",
    gateway_token_scopes=("aria-tools/invoke",),
    gateway_token_audience="aria-tools",  # nosec B106 # the Gateway token's audience, a public resource-server name
)


class TestGatewayTransport:
    def _capture(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            "core.builder.gateway_transport",
            lambda url, source, headers: captured.update(url=url, source=source, headers=headers) or "transport",
        )

        class FakeMCP:
            def __init__(self, factory, **kwargs):
                self.factory = factory
                captured["mcp"] = kwargs

        monkeypatch.setattr("core.builder.MCPClient", FakeMCP)
        return captured

    def _client(self, monkeypatch, forward_headers):
        captured = self._capture(monkeypatch)
        builder = SessionBuilder(make_config(**GATEWAY))
        client = builder._build_mcp_client(builder._build_downstream_token(), "a" * 64, forward_headers)
        client.factory()  # Strands calls the factory when it connects
        return captured

    def test_actor_and_custom_headers_reach_the_gateway(self, monkeypatch):
        captured = self._client(monkeypatch, {"x-locale": "ko-KR"})
        assert captured["headers"] == {"x-locale": "ko-KR", "x-aria-actor-id": "a" * 64}

    def test_the_actor_header_is_sent_without_custom_headers(self, monkeypatch):
        assert self._client(monkeypatch, {})["headers"] == {"x-aria-actor-id": "a" * 64}

    @pytest.mark.parametrize("name", ["X-Aria-Actor-Id", "x-aria-actor-id", "Authorization", "AUTH", "X-Aria-Tenant"])
    def test_forward_headers_cannot_set_reserved_headers(self, monkeypatch, name):
        # HTTPX would send a differently cased X-Aria-Actor-Id next to the agent's own.
        log = Log()
        monkeypatch.setattr("core.builder.logger", log)
        captured = self._client(monkeypatch, {name: "spoofed", "x-locale": "ko-KR"})
        assert captured["headers"] == {"x-locale": "ko-KR", "x-aria-actor-id": "a" * 64}
        assert ("warning", "forward_header_dropped", {"header": name.lower()}) in log.events

    def test_the_gateway_is_called_with_the_agents_token_source(self, monkeypatch):
        captured = self._client(monkeypatch, {})
        assert captured["url"] == "https://gw/mcp" and isinstance(captured["source"], DownstreamToken)

    def test_start_up_is_bounded_and_tools_are_filtered(self, monkeypatch):
        assert self._client(monkeypatch, {})["mcp"] == {
            "startup_timeout": 10,
            "tool_filters": {"allowed": ["search_products", TOOL]},
        }

    def test_the_shoppers_jwt_never_reaches_the_gateway(self, monkeypatch):
        # A real session build connects to a fake Gateway, which records every request.
        gateway, identity = FakeGateway(), FakeIdentity()
        monkeypatch.setattr(
            "core.builder.gateway_transport",
            lambda url, source, headers: gateway_transport(url, source, headers, inner=httpx.MockTransport(gateway)),
        )
        monkeypatch.setattr("core.downstream.boto3.client", lambda *args, **kwargs: identity)

        shopper_jwt = make_token({"sub": "u"})
        session = SessionBuilder(make_config(**GATEWAY)).build(shopper_jwt, SESSION_ID, workload_token="wat")  # nosec B106 # a fake workload token
        try:
            assert sorted(session.agent.tool_names) == ["saved-items___get_saved_items", "search_products"]
        finally:
            session.agent.cleanup()

        assert gateway.methods()[:3] == ["initialize", "notifications/initialized", "tools/list"]
        for request in gateway.requests:
            assert request.headers["authorization"] == "Bearer minted-for-the-agent"
            assert request.headers["x-aria-actor-id"] == actor("u")
            assert shopper_jwt.removeprefix("Bearer ") not in str(request.headers.raw)
        # One mint, from the workload token, reused while it is fresh.
        assert identity.requests == [{
            "workloadIdentityToken": "wat",
            "resourceCredentialProviderName": "ariadev-gateway",
            "scopes": ["aria-tools/invoke"],
            "oauth2Flow": "ON_BEHALF_OF_TOKEN_EXCHANGE",
            "audiences": ["aria-tools"],
            "customParameters": {"subject_token_type": "urn:ietf:params:oauth:token-type:access_token"},  # nosec B105 # an RFC 8693 token-type URN, not a credential
        }]

    def test_gateway_tools_without_a_credential_provider_fail_closed(self):
        config = make_config(**{**GATEWAY, "gateway_credential_provider": ""})
        with pytest.raises(ValueError, match="GATEWAY_CREDENTIAL_PROVIDER"):
            SessionBuilder(config)._build_downstream_token()


class FakeIdentity:
    def __init__(self):
        self.requests = []

    def get_resource_oauth2_token(self, **request):
        self.requests.append(request)
        return {"accessToken": "minted-for-the-agent"}


class TestModel:
    def test_guardrail_applied_when_both_values_present(self):
        builder = SessionBuilder(make_config(guardrail_id="gr-1", guardrail_version="2"))
        cfg = builder._build_model().get_config()
        assert cfg["guardrail_id"] == "gr-1"
        assert cfg["guardrail_version"] == "2"

    def test_guardrail_stream_processing_is_passed_through(self):
        config = make_config(guardrail_id="gr-1", guardrail_version="2", guardrail_stream_processing="async")
        assert SessionBuilder(config)._build_model().get_config()["guardrail_stream_processing_mode"] == "async"

    def test_the_guardrail_checks_the_latest_message_and_redacts_blocked_ones(self):
        # A blocked message left in history, checked again on every call, would block every later turn.
        cfg = SessionBuilder(make_config(guardrail_id="gr-1", guardrail_version="2"))._build_model().get_config()
        assert cfg["guardrail_latest_message"] is True
        assert cfg["guardrail_redact_input"] is True and cfg["guardrail_redact_output"] is True
        assert cfg["guardrail_redact_input_message"]
        # The shopper sees the reply's replacement (done.message in sync mode), so it must read as a refusal.
        assert cfg["guardrail_redact_output_message"] == GUARDRAIL_REFUSAL

    def test_no_guardrail_options_without_a_guardrail(self):
        cfg = SessionBuilder(make_config())._build_model().get_config()
        assert not any(key.startswith("guardrail_") for key in cfg if cfg[key] is not None)

    def test_no_stream_processing_without_a_guardrail(self):
        cfg = SessionBuilder(make_config(guardrail_stream_processing="async"))._build_model().get_config()
        assert "guardrail_stream_processing_mode" not in cfg

    def test_prompt_caching_off_by_default(self):
        cfg = SessionBuilder(make_config())._build_model().get_config()
        assert not cfg.get("cache_prompt")
        assert not cfg.get("cache_tools")
        assert not cfg.get("cache_config")

    def test_caching_covers_system_tools_and_messages(self):
        # cache_enabled wires all three cache points; message caching uses the
        # "auto" strategy (resolves to Claude's cachePoint format, self-skips
        # for non-Claude). make_config's default model is Claude.
        cfg = SessionBuilder(make_config(cache_enabled=True))._build_model().get_config()
        assert cfg.get("cache_prompt") == "default"
        assert cfg.get("cache_tools") == "default"
        assert cfg.get("cache_config") is not None and cfg["cache_config"].strategy == "auto"

    def test_gpt5_omits_unsupported_temperature(self):
        cfg = SessionBuilder(make_config(model_id="global.openai.gpt-5.6-luna"))._build_model().get_config()
        assert "temperature" not in cfg

    def test_other_model_families_keep_temperature(self):
        cfg = SessionBuilder(make_config(model_id="global.anthropic.claude-haiku-4-5-20251001-v1:0"))._build_model().get_config()
        assert cfg["temperature"] == 0.3

    def test_botocore_retries_once_with_explicit_timeouts(self):
        config = SessionBuilder(make_config())._build_model().client.meta.config
        assert config.retries == {"mode": "standard", "total_max_attempts": 2}
        assert (config.connect_timeout, config.read_timeout) == (5, 60)

    def test_the_agent_retries_throttling_within_seconds(self):
        # Strands' default is 6 attempts 4-64 s apart (about two minutes of sleeping).
        agent = SessionBuilder(make_config()).build(make_token({"sub": "u"}), SESSION_ID).agent
        strategy = agent._retry_strategy
        assert isinstance(strategy, ModelRetryStrategy)
        assert (strategy._max_attempts, strategy._initial_delay, strategy._max_delay) == (3, 1, 4)
        assert [strategy._calculate_delay(attempt) for attempt in range(2)] == [1, 2]


class TestHooks:
    GUARDRAIL = dict(guardrail_id="gr-1", guardrail_version="2")

    def safety(self, **overrides) -> ToolSafety:
        hook, _ = SessionBuilder(make_config(**overrides))._build_hooks()
        return hook

    def test_every_session_closes_cut_off_turns(self):
        from core.history import InterruptedTurns

        _, history = SessionBuilder(make_config())._build_hooks()
        assert isinstance(history, InterruptedTurns)

    def test_gateway_results_are_screened_with_the_models_guardrail(self):
        screen = self.safety(screen_tool_results=True, **self.GUARDRAIL).screen
        assert isinstance(screen, GuardrailScreen)
        assert (screen.guardrail_id, screen.guardrail_version) == ("gr-1", "2")

    @pytest.mark.parametrize(
        "overrides", [dict(screen_tool_results=False, **GUARDRAIL), dict(screen_tool_results=True)]
    )
    def test_no_screening_unless_enabled_with_a_guardrail(self, overrides):
        assert self.safety(**overrides).screen is None

    def test_the_budget_and_allowlist_come_from_config(self):
        safety = self.safety(allowed_tools=("search_products",), max_tool_calls=3)
        assert (safety.allowed, safety.max_calls) == (frozenset({"search_products"}), 3)


class TestRefreshIdentity:
    def test_each_request_hands_its_workload_token_to_the_token_source(self):
        from core.session import Session

        downstream = SessionBuilder(make_config(**GATEWAY))._build_downstream_token()
        session = Session(object(), "a" * 64, downstream)
        session.refresh_identity("wat-1")
        session.refresh_identity("wat-2")
        assert downstream._workload_token == "wat-2"  # nosec B105 # a fake workload token

    def test_sessions_without_gateway_tools_have_no_token_source(self):
        session = SessionBuilder(make_config()).build(make_token({"sub": "u"}), SESSION_ID)
        assert session.downstream is None
        session.refresh_identity("wat")  # a no-op, not an error

