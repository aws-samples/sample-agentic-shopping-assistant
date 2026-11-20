# packages/agent

The shopping agent: a Strands agent on AgentCore Runtime. `src/app.py` is the entry point; `src/core/builder.py` assembles a session.

## Rules

- One lazy Session per Runtime container; AgentCore owns session lifecycle. No session map or database, and a failed build must not poison the singleton.
- Check the principal-bound session ID on every invocation, cached sessions included. Identity rules live in `../contracts`.
- The shopper's JWT never leaves the agent: read it only for the actor ID and session ownership.
  - Gateway calls carry the agent's own token (`src/core/downstream.py`, `GatewayTransport` in `src/core/transport.py`) and `x-aria-actor-id`.
  - Keep the transport tests and the test that the shopper's JWT never reaches the Gateway (`tests/unit/test_builder.py`).
- Frequent tools that need no per-user authorization are local tools in `src/tools/`. `search_products` (`ProductSearchTool`) takes its schema from the catalog definition in `CATALOG_DEFINITION`, and `aria_catalog` checks its arguments and builds the query: never hand-write its schema.
- Keep the system prompt free of user-specific interpolation. Treat tool and Memory content as untrusted.
- Allowlists, budgets and argument limits are enforced in `src/core/safety.py`, not in the prompt.
- Use `BedrockModel` (Converse). OpenAI models get no explicit cache checkpoints.
- AgentCore Memory is the only memory, and every deployment has it: use it whenever `AGENTCORE_MEMORY_ID` is set. Keep `batch_size=1` and namespaces in step with infra and contracts; never rely on shutdown to persist.
- Images are ARM64 and start through `opentelemetry-instrument`. Content capture stays explicitly gated.

## Commands

- `uv sync --frozen` and `uv run --frozen pytest`. Tests never call AWS.
- The image build context is `packages/`, not this directory ([container builds](../../docs/guides/release.md#container-build-and-runtime-layout)).
- `evals/` runs billed dataset evaluations against a deployed Runtime. They're never part of `pnpm check` ([evaluations](../../docs/guides/evaluations.md)).

## Read

- [Identity and tokens](../../docs/design-docs/identity-and-tokens.md), [where a tool belongs](../../docs/design-docs/tool-placement.md)
- [Streaming and latency](../../docs/design-docs/streaming-and-latency.md), [memory](../../docs/design-docs/memory.md), [model caching](../../docs/design-docs/model-caching.md)
- [Shopping conversation spec](../../docs/product-specs/shopping-conversation.md), [add a local tool](../../docs/guides/add-a-local-tool.md), [catalog search](../../docs/design-docs/catalog-search.md)
