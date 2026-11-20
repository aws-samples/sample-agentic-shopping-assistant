# packages/api

The FastAPI service on ECS behind the load balancer. It verifies the shopper's JWT on every protected call, issues and checks session IDs, serves history from Memory, invokes the Runtime and streams SSE to the browser. [README.md](README.md) lists the endpoints and configuration.

## Rules

- Verify JWT access tokens before every protected operation: exact issuer, audience or client, and scope. Never trust a client-supplied user ID.
- Session IDs are server-generated and principal-bound (`../contracts`). Reject any ID that isn't bound to the caller.
- `src/runtime_client.py` sends only bearer authorization, SSE acceptance and the Runtime session header. It forwards no custom browser headers; changing that is a design change ([identity and tokens](../../docs/design-docs/identity-and-tokens.md)).
- Memory history and deletion SDK calls run in worker threads. Keep request handlers non-blocking.
- Log outcomes, durations and IDs. Never log tokens.

## Commands

- `uv run --frozen pytest`. Tests never call AWS.
- Build the image from the repository root with the `packages/` context ([container builds](../../docs/guides/release.md#container-build-and-runtime-layout)).

## Read

- [Sign-in and sessions spec](../../docs/product-specs/sign-in-and-sessions.md)
- [Streaming and latency](../../docs/design-docs/streaming-and-latency.md), [SECURITY.md](../../docs/SECURITY.md)
