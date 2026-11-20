# Add a local tool

[한국어](add-a-local-tool.ko.md)

1. **Write the tool** in `packages/agent/src/tools/`, as a Strands `@tool` function. `search_products` is an `AgentTool` class only because its schema is generated from the catalog definition; follow its error handling:
   - Treat every argument as untrusted. Validate values and cap sizes; search caps `limit` at 20.
   - Raise `ValueError`, or a subclass such as the catalog's `CatalogQueryError`, only when the arguments are wrong, with a message the model can act on, such as the list of allowed values.
   - Catch every other failure, log an event such as `product_search_failed` with no argument values, and raise a fixed message, as in `RuntimeError("Product search failed")`. The model then stops retrying a call that can't succeed.
   - Return `product.v1` if the web should show product cards.
   - Give `@tool` its `name`, `description` and `inputSchema` as literals, or as names bound to literals at the module's top level. `scripts/gen_docs.py` reads them from the source for the [tool catalog](../generated/tool-catalog.md), and never runs the module.
2. **Register it** in `SessionBuilder._build_tools()` (`packages/agent/src/core/builder.py`). A registered tool missing from `ALLOWED_TOOLS` is not offered to the model, and the session logs `local_tool_not_allowed`.
3. **Allow it:** add its name to `Config.local_tools` (`packages/infra/config/loader.py`). That list becomes `ALLOWED_TOOLS`. `ToolSafety` enforces it, together with the per-turn call budget (`runtime.maxToolCalls`), the 8 KiB argument limit and the 64 KiB result limit, and logs each refusal as `tool_denied`.
4. **Grant only what it needs** to the Runtime role (`packages/infra/stacks/agent_runtime_stack.py`); see `_grant_product_search()`.
5. **Test it** through Strands' own call path, `tool.stream(tool_use, {})`. See `packages/agent/tests/unit/test_product_search_tool.py`.

A local tool runs with the Runtime's role and has no token for the shopper. If it needs the shopper's identity or custom headers, read them as [identity and tokens](../design-docs/identity-and-tokens.md#custom-headers) shows. A tool that must act as the shopper on another service belongs behind the Gateway.
