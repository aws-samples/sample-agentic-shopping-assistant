# Streaming and latency

Status: verified

How a reply reaches the shopper, and where the time goes.

## The path

1. The web app posts the message to the API (`/v1/invocations`) and reads a server-sent event stream.
2. The API invokes the Runtime and relays each event as an SSE frame, with exactly one terminal frame (`done` or `error`) per invocation. Its own failures become `upstream` or `deadline` errors.
3. The agent turns Strands events into the turn contract (`aria_contracts.events`) in `for_client()`, `packages/agent/src/app.py`. Only five frame types leave the container: `textDelta`, `toolUse` (ID and name), `toolResult` (status, and content only for a `product.v1` result), `error` (`code`, `retryable`, `message`) and one `done` per turn. Each text token is sent once. Nothing else leaves: not the model's reasoning, the system prompt, tool inputs, other tools' results or the invocation state. Strands puts the invocation state, with the system prompt and transcript, on every text-delta event; relayed as they are, those events would put 0.8–2.4 MB on the wire for a real turn. A small turn is under 2 KB, which `test_stream_contract.py` asserts. Dropping reasoning also matters for GPT-5 on Bedrock, whose reasoning carries binary content JSON can't hold.
4. The web app renders text as it arrives, shows a step label while a tool runs, and attaches product cards when a `product.v1` result arrives. See [response layout](response-layout.md).

## Measured on Dev

| Moment | Time |
| --- | --- |
| First text of a later turn, from the send click | about 1.6–2.6 s when the model writes first |
| First text of a later turn that calls a Gateway tool before writing | about 4 s, including the call and its guardrail screening |
| Bytes on the wire for one turn | 2–8 KB |
| First text of a session's first turn, from the request | about 3.5 s |
| Session build inside that first turn | about 1.4 s |
| — minting the agent's Gateway token | about 0.75 s |
| — MCP handshake and tool listing through the Gateway, including the interceptor's cold start | about 0.6 s |
| Local `search_products` call, without a query embedding | under 10 ms |
| A Gateway tool call | 0.15–0.6 s |

Search runs in the agent because it runs on most turns. Behind the Gateway, each search would cost a Gateway tool call, interceptor and target included: 0.15–0.6 s, against under 10 ms in the agent. A hybrid OpenSearch search with a query adds one Embed v4 call, bounded at about 4 s before the search falls back to keywords ([catalog search](catalog-search.md#the-query)). See [where a tool belongs](tool-placement.md).

## The guardrail's stream mode

The Bedrock Guardrail runs on every model call and checks the shopper's latest message and the reply, not the whole conversation that Converse checks by default. Measured to the first text of a model call:

| Mode | First text |
| --- | --- |
| No guardrail | 0.76 s |
| `async` | 1.56 s |
| `async`, checking the whole conversation | 1.71 s |
| `sync`, checking the whole conversation | 2.61 s |

`sync` checks each reply before any of it streams. It's the default, and the Prod template's choice. `async` streams at once and checks in the background, so a reply that breaks a filter can show briefly before the stream is cut. The Dev template uses `async`. See [integrations](../guides/integrations.md#guardrail).

## Known costs

- **The first turn of every session mints a token and opens the Gateway connection.** Starting the session when the chat panel opens would hide it; see the [tech-debt tracker](../exec-plans/tech-debt-tracker.md).
- **A session whose Gateway connection fails doesn't start at all,** local search included. This is tracked there too.
