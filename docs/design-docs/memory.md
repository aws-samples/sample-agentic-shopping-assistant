# Memory

Status: verified

AgentCore Memory is ARIA's only conversation memory, and every deployment has one.

## What it stores

- **Events: short-term memory, always on.** Each turn is written as conversation events. A conversation therefore survives a Runtime session that idled out (15 minutes) or reached its lifetime (1 hour), and the session list ("지난 대화"), history and `DELETE /v1/memory` work on every stage. Raw events expire after `memory.eventExpiryDays`: 90 days by default, 7 to 365.
- **Derived records: long-term memory, off by default.** With `memory.longTerm = true`, extraction strategies derive records that are retrieved into later turns. They're derived data about each shopper, so long-term memory is opt-in; turn it on in Prod only after a privacy review. Each strategy maps to one retrieval namespace:

| Strategy | Namespace | Holds |
| --- | --- | --- |
| `semantic` | facts | Facts about the shopper |
| `summarization` | per-session summaries | Session summaries |
| `userPreference` | preferences | Cross-session preferences |

With `longTerm = true`, at least one strategy must be on. Turning long-term memory or a strategy on or off updates the Memory resource in place. Only its name and encryption key are create-only, so the conversation history is kept.

Short-term memory has no switch. The agent and the API use Memory whenever `AGENTCORE_MEMORY_ID` is set, and every deployment sets it.

## Whose memory it is

Memory is keyed by the actor ID: the SHA-256 of the verified issuer, subject and trusted tenant claim (`packages/contracts`). Changing the issuer (switching IdPs, for example) or `auth.tenantClaim` gives every shopper a new actor ID, so their earlier conversations and memory records are no longer found. The API and the Runtime use the same derivation, and clients can't supply an actor ID. Namespaces are kept in step across `packages/agent/src/core/memory.py`, the Runtime stack and the contracts package.

## Rules

- **Write with `batch_size=1`.** AgentCore Memory gives no flush guarantee at shutdown, so batching would lose the last turn (`packages/agent/src/core/builder.py`).
- **Don't rely on shutdown to persist anything.**
- **Treat retrieved memory as untrusted input,** like tool output. It goes into the turn as context, never as instructions.
- **Turning long-term memory or a strategy off doesn't delete its derived records,** and raw event expiry isn't a deletion guarantee for derived records. Follow the deletion procedure in [operations](../guides/operations.md#memory-and-pii).
