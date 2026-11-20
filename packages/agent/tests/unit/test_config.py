import pytest

from core.config import AgentConfig

GATEWAY_URL = "https://gw-1.gateway.bedrock-agentcore.ap-northeast-2.amazonaws.com/mcp"
WITH_GATEWAY_TOOL = "search_products,saved-items___get_saved_items"


@pytest.fixture(autouse=True)
def clear_env(monkeypatch):
    for key in (
        "GATEWAY_URL",
        "MCP_SSM_PREFIX",
        "ALLOWED_TOOLS",
        "MODEL_ID",
        "MODEL_MAX_TOKENS",
        "MODEL_TEMPERATURE",
        "AGENTCORE_MEMORY_ID",
        "AGENTCORE_MEMORY_STRATEGIES",
        "GATEWAY_ENABLED",
        "FORWARD_HEADERS",
        "GUARDRAIL_ID",
        "GUARDRAIL_VERSION",
        "GUARDRAIL_STREAM_PROCESSING",
        "SCREEN_TOOL_RESULTS",
        "SEARCH_PROVIDER",
        "GATEWAY_AUTH_FLOW",
        "GATEWAY_CREDENTIAL_PROVIDER",
        "GATEWAY_TOKEN_SCOPES",
        "GATEWAY_TOKEN_AUDIENCE",
        "AWS_REGION",
    ):
        monkeypatch.delenv(key, raising=False)


class Log:
    def __init__(self):
        self.events = []

    def __getattr__(self, level):
        return lambda event, **fields: self.events.append((level, event, fields))


def no_ssm(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("SSM must not be called")

    monkeypatch.setattr("boto3.client", boom)


class FakeSSM:
    def __init__(self, value):
        self.value = value
        self.names = []

    def get_parameter(self, Name):  # noqa: N803 — boto3 kwarg casing
        self.names.append(Name)
        return {"Parameter": {"Value": self.value}}


class TestGatewayUrl:
    def test_gateway_url_env_short_circuits_ssm(self, monkeypatch):
        no_ssm(monkeypatch)
        monkeypatch.setenv("ALLOWED_TOOLS", WITH_GATEWAY_TOOL)
        monkeypatch.setenv("GATEWAY_URL", GATEWAY_URL)
        monkeypatch.setenv("AWS_REGION", "ap-northeast-2")

        config = AgentConfig.from_env()

        assert config.gateway_url == GATEWAY_URL
        assert config.region == "ap-northeast-2"

    def test_reads_gateway_url_from_ssm_when_env_absent(self, monkeypatch):
        ssm, captured = FakeSSM(GATEWAY_URL), {}
        monkeypatch.setattr("boto3.client", lambda service, **kwargs: captured.update(service=service, **kwargs) or ssm)
        monkeypatch.setenv("ALLOWED_TOOLS", WITH_GATEWAY_TOOL)
        monkeypatch.setenv("MCP_SSM_PREFIX", "/mcp/endpoints/test")

        config = AgentConfig.from_env()

        assert config.gateway_url == GATEWAY_URL
        assert ssm.names == ["/mcp/endpoints/test/unified"]
        # Start-up waits on this call, so it is bounded.
        assert captured["service"] == "ssm"
        assert (captured["config"].connect_timeout, captured["config"].read_timeout) == (3, 10)
        assert captured["config"].retries == {"mode": "standard", "total_max_attempts": 3}

    @pytest.mark.parametrize("tools", ["", "search_products"])
    def test_no_gateway_tool_means_no_lookup_and_no_gateway(self, monkeypatch, tools):
        no_ssm(monkeypatch)
        monkeypatch.setenv("ALLOWED_TOOLS", tools)
        monkeypatch.setenv("MCP_SSM_PREFIX", "/mcp/endpoints/test")
        assert AgentConfig.from_env().gateway_url == ""

    def test_gateway_disabled_skips_ssm_and_leaves_url_empty(self, monkeypatch):
        no_ssm(monkeypatch)
        monkeypatch.setenv("ALLOWED_TOOLS", WITH_GATEWAY_TOOL)
        monkeypatch.setenv("GATEWAY_ENABLED", "false")
        monkeypatch.setenv("MCP_SSM_PREFIX", "/mcp/endpoints/test")
        assert AgentConfig.from_env().gateway_url == ""

    def test_gateway_tools_need_a_url_or_a_prefix(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_TOOLS", WITH_GATEWAY_TOOL)
        with pytest.raises(ValueError, match="MCP_SSM_PREFIX"):
            AgentConfig.from_env()

    @pytest.mark.parametrize(
        "url",
        [
            "http://gw-1.gateway.bedrock-agentcore.ap-northeast-2.amazonaws.com/mcp",  # not https
            "https://gateway.example.com/mcp",
            "https://gw.amazonaws.com.attacker.example/mcp",  # amazonaws.com is not the host's suffix
            "https://gw.amazonaws.com@attacker.example/mcp",  # userinfo, host is attacker.example
            "https://attacker.example\n.amazonaws.com/mcp",  # rejected by the client's URL parser
            "https://amazonaws.com/mcp",
            "https://abc123.execute-api.us-west-2.amazonaws.com/mcp",  # anyone can create one
            "https://my-bucket.s3.us-west-2.amazonaws.com/mcp",  # likewise
            "https://gw.gateway.bedrock-agentcore.us-west-2.amazonaws.com.attacker.example/mcp",
            "not a url",
        ],
    )
    @pytest.mark.parametrize("source", ["env", "ssm"])
    def test_a_gateway_url_off_aws_is_refused(self, monkeypatch, url, source):
        log = Log()
        monkeypatch.setattr("core.config.logger", log)
        monkeypatch.setenv("ALLOWED_TOOLS", WITH_GATEWAY_TOOL)
        if source == "env":
            monkeypatch.setenv("GATEWAY_URL", url)
        else:
            monkeypatch.setenv("MCP_SSM_PREFIX", "/mcp/endpoints/test")
            monkeypatch.setattr("boto3.client", lambda *a, **k: FakeSSM(url))

        assert AgentConfig.from_env().gateway_url == ""
        assert [(level, event, fields["source"]) for level, event, fields in log.events] == [
            ("warning", "gateway_url_rejected", source)
        ]


class TestFromEnv:

    def test_memory_is_used_whenever_an_id_is_set(self, monkeypatch):
        # Memory is used whenever AGENTCORE_MEMORY_ID is set; with no strategies it is short-term only.
        monkeypatch.setenv("GATEWAY_URL", "https://gw/mcp")
        monkeypatch.setenv("AGENTCORE_MEMORY_ID", "mem-1")
        config = AgentConfig.from_env()
        assert config.memory_id == "mem-1" and config.memory_strategies == ()

    def test_memory_strategies_parsed_from_csv(self, monkeypatch):
        monkeypatch.setenv("GATEWAY_URL", "https://gw/mcp")
        monkeypatch.setenv("AGENTCORE_MEMORY_STRATEGIES", "semantic,summarization")
        config = AgentConfig.from_env()
        assert config.memory_strategies == ("semantic", "summarization")

    def test_no_strategies_yields_empty_tuple(self, monkeypatch):
        monkeypatch.setenv("GATEWAY_URL", "https://gw/mcp")
        assert AgentConfig.from_env().memory_strategies == ()

    @pytest.mark.parametrize("value,expected", [(None, False), ("true", True), ("TRUE", True), ("false", False)])
    def test_screen_tool_results(self, monkeypatch, value, expected):
        if value is not None:
            monkeypatch.setenv("SCREEN_TOOL_RESULTS", value)
        assert AgentConfig.from_env().screen_tool_results is expected

    def test_forward_headers_parsed_from_csv(self, monkeypatch):
        monkeypatch.setenv("GATEWAY_URL", "https://gw/mcp")
        monkeypatch.setenv("FORWARD_HEADERS", "x-tenant-id,x-locale")
        assert AgentConfig.from_env().forward_headers == ("x-tenant-id", "x-locale")

    def test_forward_headers_empty_by_default(self, monkeypatch):
        monkeypatch.setenv("GATEWAY_URL", "https://gw/mcp")
        assert AgentConfig.from_env().forward_headers == ()

    @pytest.mark.parametrize("name", ["x-aria-actor-id", "X-Aria-Actor-Id", "X-Aria-Tenant", "Authorization", "AUTH"])
    def test_reserved_forward_headers_fail_at_start(self, monkeypatch, name):
        # Forwarded headers reach local tools' invocation_state with the caller's values, so the
        # agent refuses to start rather than let a shopper set one of these for local tools.
        monkeypatch.setenv("GATEWAY_URL", "https://gw/mcp")
        monkeypatch.setenv("FORWARD_HEADERS", f"x-tenant-id,{name}")
        with pytest.raises(ValueError, match=f"FORWARD_HEADERS can't name {name}:"):
            AgentConfig.from_env()

    def test_an_x_aria_lookalike_is_still_forwarded(self, monkeypatch):
        monkeypatch.setenv("GATEWAY_URL", "https://gw/mcp")
        monkeypatch.setenv("FORWARD_HEADERS", "x-arial-font,x-tenant-aria")
        assert AgentConfig.from_env().forward_headers == ("x-arial-font", "x-tenant-aria")

    @pytest.mark.parametrize(
        "env,expected",
        [
            ({"GUARDRAIL_ID": "gr-1", "GUARDRAIL_VERSION": "2"}, ("gr-1", "2")),
            # Neither set: no guardrail. One without the other fails at start-up (test_partial_guardrail_fails_closed).
            ({}, (None, None)),
        ],
    )
    def test_guardrail_requires_both_or_neither(self, monkeypatch, env, expected):
        monkeypatch.setenv("GATEWAY_URL", "https://gw/mcp")
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        config = AgentConfig.from_env()

        assert (config.guardrail_id, config.guardrail_version) == expected

    def test_config_is_immutable(self, monkeypatch):
        monkeypatch.setenv("GATEWAY_URL", "https://gw/mcp")
        config = AgentConfig.from_env()
        with pytest.raises(Exception):
            config.model_id = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("value,expected", [(None, "sync"), ("sync", "sync"), ("async", "async")])
def test_guardrail_stream_processing(monkeypatch, value, expected):
    monkeypatch.setenv("GATEWAY_URL", "https://gw/mcp")
    if value is not None:
        monkeypatch.setenv("GUARDRAIL_STREAM_PROCESSING", value)
    assert AgentConfig.from_env().guardrail_stream_processing == expected


def test_unknown_guardrail_stream_processing_fails_closed(monkeypatch):
    monkeypatch.setenv("GATEWAY_URL", "https://gw/mcp")
    monkeypatch.setenv("GUARDRAIL_STREAM_PROCESSING", "ASYNCHRONOUS")
    with pytest.raises(ValueError, match="GUARDRAIL_STREAM_PROCESSING"):
        AgentConfig.from_env()


@pytest.mark.parametrize("value,expected", [(None, "fixture"), ("opensearch", "opensearch"), ("disabled", "disabled")])
def test_search_provider(monkeypatch, value, expected):
    monkeypatch.setenv("GATEWAY_URL", "https://gw/mcp")
    if value is not None:
        monkeypatch.setenv("SEARCH_PROVIDER", value)
    assert AgentConfig.from_env().search_provider == expected


def test_unknown_search_provider_fails_closed(monkeypatch):
    monkeypatch.setenv("GATEWAY_URL", "https://gw/mcp")
    monkeypatch.setenv("SEARCH_PROVIDER", "elastic")
    with pytest.raises(ValueError, match="SEARCH_PROVIDER"):
        AgentConfig.from_env()


def test_gateway_token_settings(monkeypatch):
    monkeypatch.setenv("GATEWAY_URL", "https://gw/mcp")
    monkeypatch.setenv("GATEWAY_AUTH_FLOW", "M2M")
    monkeypatch.setenv("GATEWAY_CREDENTIAL_PROVIDER", "ariadev-gateway")
    monkeypatch.setenv("GATEWAY_TOKEN_SCOPES", "aria-tools/invoke other/scope")
    monkeypatch.setenv("GATEWAY_TOKEN_AUDIENCE", "aria-tools")
    config = AgentConfig.from_env()
    assert (config.gateway_auth_flow, config.gateway_credential_provider) == ("M2M", "ariadev-gateway")
    assert config.gateway_token_scopes == ("aria-tools/invoke", "other/scope")
    assert config.gateway_token_audience == "aria-tools"  # nosec B105 # the Gateway token's audience, a public resource-server name


def test_gateway_auth_flow_defaults_to_on_behalf_of(monkeypatch):
    monkeypatch.setenv("GATEWAY_URL", "https://gw/mcp")
    assert AgentConfig.from_env().gateway_auth_flow == "ON_BEHALF_OF_TOKEN_EXCHANGE"


def test_unknown_gateway_auth_flow_fails_closed(monkeypatch):
    monkeypatch.setenv("GATEWAY_URL", "https://gw/mcp")
    monkeypatch.setenv("GATEWAY_AUTH_FLOW", "USER_FEDERATION")
    with pytest.raises(ValueError, match="GATEWAY_AUTH_FLOW"):
        AgentConfig.from_env()


def test_partial_guardrail_fails_closed(monkeypatch):
    monkeypatch.setenv("GATEWAY_URL", "https://gateway.example/mcp")
    monkeypatch.setenv("GUARDRAIL_ID", "gr-1")
    with pytest.raises(ValueError):
        AgentConfig.from_env()


def test_caching_allowed_for_claude(monkeypatch):
    monkeypatch.setenv("GATEWAY_URL", "https://gateway.example/mcp")
    monkeypatch.setenv("MODEL_CACHE_ENABLED", "true")
    monkeypatch.setenv("MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")
    assert AgentConfig.from_env().cache_enabled is True


@pytest.mark.parametrize(
    "model_id",
    [
        "global.openai.gpt-5.6-luna",           # OpenAI rejects cachePoints on Converse (verified live)
        "us.meta.llama3-3-70b-instruct-v1:0",   # no cache-point support
    ],
)
def test_caching_rejected_for_non_claude_on_converse(monkeypatch, model_id):
    monkeypatch.setenv("GATEWAY_URL", "https://gateway.example/mcp")
    monkeypatch.setenv("MODEL_CACHE_ENABLED", "true")
    monkeypatch.setenv("MODEL_ID", model_id)
    with pytest.raises(ValueError, match="caching"):
        AgentConfig.from_env()
