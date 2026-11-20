# Product cards

Status: shipped

How products appear in Aria's answers: the `product.v1` result envelope, the card, citations, the "비슷한 상품 찾기" action, and what the web shows for tool results that aren't products. The rendering pipeline is described in [response layout](../design-docs/response-layout.md).

## What the shopper sees

- A product appears as a card only where Aria cites it on a line of its own, between the section heading and its explanation. A returned product Aria doesn't cite isn't shown.
- A citation inside a sentence shows the product's name.
- If Aria cites an ID no result returned, the shopper sees "검색 결과에서 확인할 수 없는 상품이에요." in place of the card, or the muted label "확인할 수 없는 상품" inside a sentence.
- While the answer streams, a citation still being written, such as "[[Product:sam", is held back, so it never flashes on screen.
- A conversation reopened from "지난 대화" is text only: standalone citations are dropped quietly, inline ones read "이 상품", and there are no cards.

A card shows:

| Part of the card | Field |
| --- | --- |
| A colour swatch standing in for a photo, with the year on it | `baseColour`, `year` |
| An uppercase label such as "APPAREL · WINTER" | `masterCategory`, `season` |
| The title | `productDisplayName` |
| The category path: `subCategory`, or `subCategory › articleType` when they differ | `subCategory`, `articleType` |
| A colour dot and name, the gender, a usage tag | `baseColour`, `gender`, `usage` |
| The "비슷한 상품 찾기 →" button | `productDisplayName` |

Cards have no price, stock, rating, image or link: `product.v1` doesn't carry them.

## The product.v1 envelope

A tool that wants cards returns this object as the JSON text of its result:

```json
{
  "schemaVersion": "product.v1",
  "count": 1,
  "products": [
    {
      "id": "sample-1",
      "productDisplayName": "겨울 자켓 Winter Jacket",
      "gender": "Men",
      "masterCategory": "Apparel",
      "subCategory": "Topwear",
      "articleType": "Topwear",
      "baseColour": "Blue",
      "season": "Winter",
      "year": 2026,
      "usage": "Casual"
    }
  ]
}
```

| Field | Type | Rule |
| --- | --- | --- |
| `schemaVersion` | string | Exactly `product.v1` |
| `count` | integer | The number of products. Producers set it; the web doesn't check it |
| `products` | array | Required. An entry that fails the field check is dropped, so it gets no card. If every entry fails, or `products` isn't an array, the web treats the result as a failed call; an empty array is a search with no matches |
| `id` | string | Required. Only IDs of ASCII letters, digits and `._:-`, at most 128 characters, can be cited |
| `productDisplayName`, `gender`, `masterCategory`, `subCategory`, `articleType`, `baseColour`, `season`, `usage` | string | Required |
| `year` | number | Required; a string fails the check |

The producers are `search_products`, through `aria_catalog.result()`, and the saved-items sample, which returns the object directly and lets the Gateway wrap it as MCP content ([Lambda target contract](../guides/gateway-onboarding.md#lambda-target-contract)). Any Gateway tool that returns this envelope gets cards the same way.

## Rules

- Cards come only from validated `product.v1` results already in the open conversation. Whatever ID the model writes, no result means no card.
- Citations resolve by ID against every product returned in the conversation so far. A later result with the same ID replaces the earlier product.
- Product strings and model text are escaped, and Markdown runs with HTML disabled, so neither the model nor a catalog field can inject markup.
- The swatch colour comes from a fixed list of colour names, such as "navy blue" and "sea green"; an unknown colour gets a neutral stone.
- A product can be cited more than once; each standalone citation is a card.
- A tool result over 65,536 bytes of JSON is replaced by an error before it reaches the web, and the web rejects a stream frame over 262,144 characters.
- The citation format is shared by the prompt and the web parser, so release the agent and the web together: a web build that doesn't parse the format shows the citations as raw text.

## Find similar ("비슷한 상품 찾기")

- Every card has a "비슷한 상품 찾기 →" button. It sends `비슷한 상품 보여줘: ${productDisplayName}` as the shopper's next message, for example "비슷한 상품 보여줘: Cotton Shirt".
- It is an ordinary message: it appears as the shopper's bubble, and Aria searches as for any request. No ID or attributes travel with it; Aria works from the name and the earlier results in the conversation.
- If Aria is still answering, the message waits and is sent when the answer finishes.

## Tool results that aren't products

| Result | What the shopper sees |
| --- | --- |
| A failed call (`status: "error"`) | Nothing in the transcript. While Aria corrects the call or explains, the status line reads "검색 조건을 다시 확인하고 있어요" after a failed `search_products` call and "답변을 정리하고 있어요" after any other |
| Text or JSON that isn't `product.v1`, or a result with no text | Nothing in the transcript, and nothing of it is kept; the shopper sees only what Aria writes about it. Once the result arrives, the status line reads "답변을 정리하고 있어요" |
| A `product.v1` result with invalid products | The invalid products are dropped and never get a card; the valid ones can be cited. If none of its products is valid, or `products` isn't an array, it counts as a failed call |

A valid `product.v1` result is never shown by itself; only the products Aria cites are.

## Acceptance checks

Automated, with `pnpm --filter nordly-web test` (part of `pnpm check`):

- A citation on its own line becomes a card between the heading and the explanation; a citation inside a sentence stays in the prose (`packages/web/tests/citations.test.ts`).
- Every fixture ID can be cited, and the prompt's own example yields one card for `sample-1` (same file).
- An inline citation shows the returned product's name; an unknown ID shows "확인할 수 없는 상품"; in restored history it reads "이 상품" (same file).
- An unfinished citation is never shown while streaming (same file).
- Catalog strings and model markup are escaped (same file).
- Citations resolve only against validated results; a failed tool call is hidden, and so is every tool result other than `product.v1` (same file).
- The saved-items sample's product shape becomes product data, other tool output (MCP text included) is kept from the shopper, and a tool error isn't shown as an empty match (`packages/web/tests/contracts.test.ts`).

Manual, on a Dev stage:

- "비슷한 상품 찾기" on the `sample-2` card sends "비슷한 상품 보여줘: Cotton Shirt", and Aria answers it with a new search.
- A cited ID that no result returned shows "검색 결과에서 확인할 수 없는 상품이에요." instead of a card.

## Where it lives in the code

| What | Path |
| --- | --- |
| Envelope and field checks, and what the transcript keeps of each tool result | `packages/web/app/utils/message.ts`, `packages/web/app/utils/validation.ts` |
| Citation parsing and inline rendering | `packages/web/app/utils/citations.ts` |
| Answer layout: text and card slots | `packages/web/app/components/chat/Answer.vue` |
| The card and "비슷한 상품 찾기" | `packages/web/app/components/chat/ProductCard.vue` |
| Turn grouping and products by ID | `packages/web/app/utils/turns.ts` |
| The status line and its step labels | `packages/web/app/components/chat/Content.vue`, `packages/web/app/utils/activity.ts` |
| Product type | `packages/web/app/types/domain.ts` |
| Envelope producer for search | `packages/catalog/src/aria_catalog/__init__.py` |
| Citation rules for the model | `packages/agent/src/prompts/system.md` |

## Known gaps

- After the shopper reopens a conversation, its earlier results aren't in the browser. If Aria cites one of those products in a new turn, the shopper sees "검색 결과에서 확인할 수 없는 상품이에요." instead of a card.
- Search results are checked against the citation rule in `aria_catalog`, but a Gateway tool's `product.v1` IDs aren't: the web checks only that `id` is a string. Only IDs of ASCII letters, digits and `._:-`, up to 128 characters, can be cited; a citation of any other ID shows as raw `[[Product:…]]` text.
- Card values are shown untranslated, so English catalog values appear in the Korean UI.
- Renderers for other tool results are possible future work ([response layout](../design-docs/response-layout.md)).
