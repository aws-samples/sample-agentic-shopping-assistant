# Saved items

Status: partial

`saved-items___get_saved_items` is a sample Gateway tool. It returns a stable, made-up list of "saved" products for each shopper, so that the Gateway path with the shopper's identity runs end to end. The sample ships in the Dev template. Real saved items, and any way to save one, are planned.

## What the shopper sees

- On a stage with the sample, the shopper asks "내가 저장한 상품 보여줘". The status line reads "저장한 상품을 확인하고 있어요", and Aria answers with cards for the items it cites.
- The items are drawn from the example catalog's 150 products and chosen from the shopper's actor ID: up to 5 by default. The same shopper always gets the same items. They are not anything the shopper saved.
- The shopper can't save or remove an item: no tool does it, and the prompt forbids writes.
- Where the sample isn't deployed, as in every Prod stage, Aria has no saved-items tool.

## Why the sample exists

Product search runs in the agent and needs no identity, so it doesn't use the Gateway. The sample exercises the Gateway path with a tool that has to know who the shopper is:

1. The agent mints its own Gateway token from the shopper's sign-in (an on-behalf-of exchange by default) and names the shopper in `x-aria-actor-id`. Agent code sets both; the model sets neither.
2. The Gateway accepts only the agent's token, and Cedar authorizes the call when the policy engine is on.
3. The REQUEST interceptor requires a well-formed `x-aria-actor-id` and, with an on-behalf-of token, checks it against the token's subject.
4. The Lambda reads the actor ID from the propagated headers and returns `product.v1`, which the web renders as cards.

The path is verified end to end on Dev. The token and header rules are in [identity and tokens](../design-docs/identity-and-tokens.md) and [the Gateway interceptor](../design-docs/gateway-interceptor.md); where tools belong is in [tool placement](../design-docs/tool-placement.md).

## Rules for the sample

- **Tool.** The model sees `saved-items___get_saved_items`, described as "The shopper's saved items (wishlist). Use it when the shopper asks about items they saved." Its only input is `limit`, an integer: 5 by default, at most 20. There is no shopper argument.
- **Identity.** The actor ID comes only from the `x-aria-actor-id` header, matched case-insensitively, and must be 64 lowercase hex characters. The interceptor denies a call without one before the Lambda runs, and the Lambda refuses it too ("Saved items need the shopper's identity").
- **Arguments.** Only `limit`. Any other argument, or a tool name other than `get_saved_items`, fails the call.
- **Selection.** The example catalog's products, mapped onto cards through the example definition, are ordered by the SHA-256 of `<actor ID>:<product id>`. The count is 1 plus the actor ID's first byte modulo the catalog size (150), capped by `limit` clamped to 1–20, so most shoppers get 5 items by default and a few get fewer.
- **Result.** The `product.v1` object, returned directly; the Gateway wraps it as MCP content ([product cards](product-cards.md)).
- **Policy.** With Cedar on, a call is permitted when `limit` is absent or 1–20, and forbidden when it is over 20 (`packages/infra/components/policies/sample/`, applied to the sample's tool only). Cedar is off in the Dev template.
- **Availability.** `gateway.savedItemsSample = true` with `gateway.mode = "managed"`. It is on in the Dev template and off by default; Prod rejects it.

## What a real implementation must do (planned)

- Read the shopper's saved items from its own store, keyed by the actor ID in `x-aria-actor-id`, and scope the query in the datastore: Cedar authorizes the action, not the rows.
- Never accept identity as a tool argument, and refuse a call without a valid actor ID.
- Return `product.v1` with valid fields and citable IDs so the items render as cards. Use the catalog's product IDs, so a saved item and a search result for the same product match.
- Return an empty envelope (`count: 0`) when the shopper has saved nothing, not an error: the web hides failed calls, and the prompt's rule for failed searches would have Aria say the catalog couldn't be checked.
- Stay bounded: at most 20 items and 64 KiB per result.
- Keep the name `get_saved_items`, or change the web's status label, the allowlist in `packages/infra/config/loader.py`, the Cedar action and the eval trajectories with it.
- Treat saving or removing an item as a write. It needs its own tool, a Cedar policy, a confirmation step and a change to the prompt's read-only rule. Onboarding a target is covered in [Gateway onboarding](../guides/gateway-onboarding.md).

## Acceptance checks

Automated, part of `pnpm check`:

- With a valid actor header, the handler returns `product.v1` cards from the example catalog, and `count` equals the number of products (`packages/mcp-servers/saved-items-lambda/tests/test_handler.py`).
- The sample draws on the whole example catalog, different shoppers get different samples, and how many items a shopper has depends on the actor ID (same file).
- The handler imports only the standard library and `aria_catalog` (same file).
- The same actor gets the same items, whatever the header's casing (same file).
- A missing, empty or malformed actor ID, or one under another header name, raises `PermissionError` (same file).
- `limit: 1` returns one item, and a large `limit` is capped at 20 (same file).
- An unknown tool, or an extra argument such as `actor`, raises `ValueError` (same file).
- The interceptor denies a `tools/call` without a valid actor ID, or whose on-behalf-of token names a different shopper (`packages/mcp-servers/gateway-interceptor/tests/test_handler.py`).
- The Gateway stack advertises `get_saved_items` (`packages/infra/tests/test_gateway_stack.py`).

On a Dev stage:

- "내가 저장한 상품 보여줘" calls `saved-items___get_saved_items`, and the answer cites only the returned items.
- A Gateway call with the shopper's own token is rejected with 403, and a `tools/call` whose `x-aria-actor-id` names someone else is denied.

## Where it lives in the code

| What | Path |
| --- | --- |
| The sample Lambda | `packages/mcp-servers/saved-items-lambda/src/handler.py` |
| Tool definition, Lambda and target | `packages/infra/stacks/gateway_stack.py` (`_create_saved_items_target`) |
| The `savedItemsSample` switch and the allowlist entry | `packages/infra/config/loader.py`, `packages/infra/config/dev.toml` |
| Interceptor | `packages/mcp-servers/gateway-interceptor/src/handler.py` |
| Cedar policies | `packages/infra/components/policies/sample/` |
| Actor header and the agent's Gateway token | `packages/agent/src/core/builder.py`, `packages/agent/src/core/downstream.py` |
| Status line label | `packages/web/app/utils/activity.ts` (`TOOL_LABELS`), `packages/web/app/utils/copy.ts` (`COPY.steps.savedItems`) |
| The example catalog the sample draws from | `packages/catalog/src/aria_catalog/example/` (`catalog.toml`, `products.json`) |

## Known gaps

- Without the sample, the prompt tells Aria to answer a saved-items request with its shopping-only decline, although the request is about shopping.
- The sample always draws from the example catalog, even when search uses another provider or catalog, so its IDs may not exist in the live catalog.
