# NORDLY API

[한국어](README.ko.md)

FastAPI validates JWT access tokens before every protected operation and calls AgentCore Runtime over asynchronous bearer HTTP. AgentCore Memory history/deletion SDK calls run in worker threads. User invocation does not require IAM invocation permission for the JWT Runtime.

## Configuration

The API runs only as the ECS service in the ApiService stack, which sets these variables from the selected `.toml`. `src/config.py` validates them during startup. Set `AGENT_RUNTIME_ARN` to a Runtime ARN, `OIDC_ISSUER` to the exact HTTPS issuer, and at least one of `OIDC_AUDIENCES` or `OIDC_ALLOWED_CLIENTS`. `OIDC_SCOPES` is a space-separated list (default `aria/chat`); audiences and allowed clients are comma-separated. `AUTH_PROVIDER` is `cognito` (default) or `oidc`; `AWS_REGION` defaults to `ap-northeast-2`.

Optional settings: `OIDC_TENANT_CLAIM`, `CORS_ALLOW_ORIGINS` (comma-separated, default `http://localhost:4000`), `REQUEST_TIMEOUT_SECONDS` (10–300, default 120), `AGENT_RUNTIME_QUALIFIER` (default `DEFAULT`) and `PORT` (default 8000). The stack always sets `AGENTCORE_MEMORY_ID`, the Memory that holds session history. Without it (outside a deployment), the session list and history are empty, and `DELETE /v1/memory` deletes nothing. Missing/invalid Runtime or identity settings fail startup; the invocation route also has a defensive 503 check for a missing Runtime ARN.

## Endpoints and streaming

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/ping` | Process health; does not test downstream integrations |
| POST | `/v1/sessions` | Create an opaque, principal-bound 97-character session ID |
| GET | `/v1/sessions` | List the caller's sessions discoverable through Memory, newest first: `session_id` and, when Memory reports it, `createdAt` (ISO 8601, UTC). A session with no event left, a deleted conversation, isn't listed |
| GET | `/v1/sessions/{id}/messages` | Return text-only user/assistant history |
| DELETE | `/v1/sessions/{id}` | Stop Runtime and delete current events/summary; retain preferences/facts |
| DELETE | `/v1/memory` | Stop discoverable sessions and delete their events and saved memories |
| POST | `/v1/invocations` | Invoke Runtime; stream by default |

Invocation JSON contains `message` (1–8000 characters), a server-generated `session_id`, and optional `stream`. The caller's identity comes only from the verified access token, never from the body, and a session ID that isn't bound to the caller is rejected. With `stream:false`, the reply is one JSON object, `{"data": "...", "stopReason": "..."}`. When the guardrail intervened, `data` is its replacement text if the agent sent one, which it does with `guardrail.streamProcessing = "sync"` (required in Prod), and empty otherwise; it never holds what streamed before the intervention. A turn that fails returns its error code as an HTTP error, in the coded body below: 403 `owner_mismatch`, 409 `busy` or `turn_limit`, 422 `invalid_request`, 429 `throttled`, 503 `init_failed`, 504 `deadline`, and 502 `internal` or `upstream`.

An HTTP error that the web acts on carries a code from `aria_contracts.events` as `{"detail": {"code": ..., "message": ...}}`: `owner_mismatch` (403) for a session ID that isn't the caller's; `invalid_request` for a body the API rejects (422, or 413 over 32 KiB); and `upstream` (503) when the identity provider is unavailable. Other errors, 401 included, carry a plain `detail` string.

SSE frames contain `version:1`, `type`, and `data`; the events are defined in `aria_contracts.events` (`packages/contracts`). `textDelta` carries `text`; `toolUse` carries `toolUseId` and `name`, never the input; `toolResult` carries `toolUseId`, `status`, and `content` only for a `product.v1` result. Every turn ends with exactly one `done` (`stopReason`, plus `message` with the guardrail's replacement text for `guardrail_intervened`) or one `error` (`code`, `retryable`, and an English `message` for developers). Error codes: `init_failed`, `owner_mismatch`, `turn_limit`, `deadline`, `busy`, `throttled`, `internal`, `invalid_request` from the agent, and from the API itself `upstream` when its call to the Runtime fails or the stream ends without a result, or `deadline` when that call times out.

`src/runtime_client.py` sends only bearer authorization, SSE acceptance, and the Runtime session header. It does not forward custom browser headers. The invocation stream has no HTTP read timeout, because the agent sends nothing while the model or a tool runs: the configurable overall request deadline bounds it, and the agent stops 10 seconds earlier so that its own error arrives first. Other Runtime calls, such as stopping a session, have a 60-second read timeout. Memory calls time out after 3 seconds to connect and 10 to read, with three attempts in all. uvicorn keeps idle connections for 155 seconds, longer than the load balancer's 150, and drains for up to 25 seconds on shutdown. Logs: `invocation_finished` (`session_id`, `status`, `ttfb_ms`, `duration_ms`), `agent_error` (`code`, `session_id`) for every error frame, and `runtime invocation failed` when the Runtime call fails. See [operations](../../docs/guides/operations.md) for deletion verification and asynchronous extraction limitations.

## Tests and image

```sh
uv run --frozen pytest
```

Build from the repository root; shared contracts require the `packages` context:

```sh
finch build -f packages/api/Dockerfile -t aria-api:local packages
```
