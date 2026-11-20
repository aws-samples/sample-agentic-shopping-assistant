# packages/contracts

The rules shared by the API and the agent:

- the actor ID: the SHA-256 of the verified issuer, subject and trusted tenant claim;
- principal-bound session IDs;
- the turn events (`aria_contracts.events`): what the agent streams and the API relays to the browser, and the error codes.

No dependencies and no AWS calls.

## Rules

- Changing a derivation changes every shopper's actor ID and Memory namespace. Treat it as a data migration, not a refactor.
- The Gateway interceptor keeps a stdlib-only copy of `actor_id`. Its tests and `tests/test_identity.py` pin the same reference values; change them together.
- Turn events carry no tool input, model reasoning, invocation state, system prompt or transcript. A tool result's content crosses only for a schema in `DISPLAY_SCHEMAS`.
- The browser, the API and the CloudWatch metric filters read the error codes. Adding one is a contract change.

## Commands

- `uv sync --frozen` and `uv run --frozen pytest`. The API and agent suites exercise these contracts end to end too (for example `packages/agent/tests/unit/test_stream_contract.py`).

## Read

- [Identity and tokens](../../docs/design-docs/identity-and-tokens.md), [SECURITY.md](../../docs/SECURITY.md), [streaming and latency](../../docs/design-docs/streaming-and-latency.md)
