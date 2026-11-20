# Strands Agents: notes

The Strands docs site publishes an index at <https://strandsagents.com/llms.txt>, and every page is also available as Markdown. Its license isn't stated, so this file links to it instead of copying it. The Python SDK is Apache-2.0.

## Pages ARIA relies on

- Tools: `@tool`, tool context, and streaming tool calls; start at the [SDK user guide](https://strandsagents.com/docs/user-guide/sdk/index.md)
- MCP: `MCPClient` over streamable HTTP, tool filters
- Hooks: `HookProvider`, used by `ToolSafety` for the allowlist, budget and argument limit
- Models: `BedrockModel` (Converse) and its cache configuration
- Session managers, used for AgentCore Memory

## Behavior ARIA depends on

Verified in this repository's tests or on Dev:

- An `AgentTool`'s `tool_spec` reaches the model as given, so its `inputSchema` must be wrapped as `{"json": {...}}`, as `aria_catalog.tool_spec()` builds it (`packages/agent/src/tools/product_search.py`). Strands passes such a tool its input as the model sent it, so the tool checks every argument itself.
- An `MCPClient` passed in `tools=[...]` starts, initializes and lists its tools while the `Agent` is constructed, not on first use. If it fails, constructing the agent raises `ToolProviderException`.
- `tool.stream(tool_use, {})` runs a tool through Strands' own call path, so unit tests exercise the same validation the agent uses (`packages/agent/tests/unit/test_product_search_tool.py`).
