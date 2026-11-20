# ARIA agent

[한국어](README.ko.md)

The fixed Strands shopping agent runs on AgentCore Runtime. Runtime manages containers and session lifetime. The application checks principal-bound session IDs before constructing/reusing the agent or reading Memory. There is no session database.

Configuration arrives through CDK environment variables. Bedrock uses Converse with a 60-second read timeout; API/Runtime enforce an overall request deadline. Prompt caching is off by default and rejected for non-Claude models when explicitly enabled. Tools are filtered to `ALLOWED_TOOLS`; hooks enforce eight calls per invocation and bounded arguments/results. The warm session permits at most 100 turns by default. The prompt treats retrieved data as untrusted and supports read-only shopping assistance.

Every deployment sets `AGENTCORE_MEMORY_ID`, and the agent writes each turn to AgentCore Memory with `batch_size=1`; no shutdown hook is relied on. With long-term memory on, relevant preferences and facts are also retrieved into each turn. `GatewayTransport` (`src/core/transport.py`) asks for the agent's own Gateway token on every request, so a refreshed token is used without reconnecting the MCP session.

```sh
uv sync --frozen
uv run --frozen pytest
```

Build from the repository root: `finch build -f packages/agent/Dockerfile -t aria-agent:local packages`. The shared contracts package is part of the build context. Startup runs the installed virtual environment directly and does not install dependencies.

AgentCore owns exporter/routing settings. `TRACE_CAPTURE_CONTENT=false` redacts sensitive Strands attributes and disables message capture; this intentionally controls content policy without replacing the sidecar's exporter endpoints. Continuous evaluation requires an explicit opt-in to conversation trace content.
