# Product specs

What ARIA does and must do, seen from the shopper's side. Use a spec to decide whether a change is correct: each one lists rules and acceptance checks you can test. How the system is built is in the [design docs](../design-docs/index.md), and the judgment behind the specs is in [product sense](../PRODUCT_SENSE.md).

| Spec | Summary | Status |
| --- | --- | --- |
| [Shopping conversation](shopping-conversation.md) | One turn: what Aria helps with and declines, Korean answers, when it searches, how answers are built, limits, what it never does | shipped |
| [Product search](product-search.md) | The `search_products` contract: the free-text query and the filters the catalog definition declares, limits, empty results, invalid values, providers, the example catalog | shipped |
| [Product cards](product-cards.md) | The `product.v1` envelope, how cards and citations render, "비슷한 상품 찾기", tool results that aren't products | shipped |
| [Saved items](saved-items.md) | The sample Gateway tool that returns a stable per-shopper sample, and what a real implementation must do | partial |
| [Sign-in and sessions](sign-in-and-sessions.md) | Managed login, requested scopes, session ownership, sign-out, token expiry, signing in again after a scope change | shipped |

## Status

- **shipped:** implemented and covered by tests or a live check on Dev.
- **partial:** some of the described behavior is implemented; the rest is marked planned.
- **planned:** not implemented.

## Keeping specs true

- Change the spec in the same commit as the behavior it describes.
- Quote UI copy and messages exactly as the code has them, and name the file.
- Mark anything not implemented as planned, and list known gaps as current behavior, not as features.
