# Core beliefs

Status: accepted

The principles ARIA is built on. When a change conflicts with one of them, the change needs a design doc that says why.

## The repository is the system of record

What isn't in the repository doesn't exist for the next person or agent. Decisions go into [design docs](index.md), verified third-party behavior into [references](../references/index.md), and known gaps into the [tech-debt tracker](../exec-plans/tech-debt-tracker.md). Chat threads and tickets are not sources of truth.

Keep entry points small. [AGENTS.md](../../AGENTS.md) is a map, not a manual: each doc covers one topic, in one language per file, and links onward.

## The shopper's token never leaves the agent

The Runtime verifies the shopper's sign-in token, and the agent reads it only to derive the actor ID and check session ownership. Tools learn who the shopper is from verified context: `agent.state` locally, `x-aria-actor-id` behind the Gateway. Identity is never a model-writable argument, and the shopper's token is never forwarded. See [identity and tokens](identity-and-tokens.md).

## Enforce in code, not in prompts

Tool allowlists, the per-turn call budget, argument size limits, session ownership, the guardrail, Cedar policies and the interceptor's actor check are deterministic. The system prompt guides the model; it never carries a control that matters.

## Put a tool where its cost and trust belong

A tool that runs on most turns and needs no per-user authorization runs in the agent, with no network hop. A tool that needs the shopper's identity, or credentials of its own, sits behind the Gateway. See [where a tool belongs](tool-placement.md).

## Deploy only what was reviewed

A deployment is synthesized from a clean commit, diffed, hashed and deployed unchanged. Nothing becomes publicly reachable without explicit approval, and production resources change only on explicit direction. See [deployment safety](deployment-safety.md).

## Claims carry their evidence

Every design doc states how and when it was verified: unit tests, synthesis, or a live run on Dev. Synthesis checks templates, not behavior, so "synth-tested" is never written as "verified". A template can pass every synth test and fail on the first live call; [deployment safety](deployment-safety.md#live-checks-after-every-deploy) gives examples.

## Fail closed on identity, visibly on everything else

A missing or mismatched identity denies the request. Other failures surface as errors the shopper or operator can act on, never as a confident wrong answer. Aria shows real catalog items, and says so when an attribute isn't in the catalog.

## Generate what code can tell you

Configuration keys, cdk-nag exceptions and tool schemas are generated into `docs/generated/` ([configuration](../generated/config-reference.md), [cdk-nag exceptions](../generated/cdk-nag-exceptions.md), [tools](../generated/tool-catalog.md)) and checked by `pnpm check`. A hand-written copy of something the code knows will drift.
