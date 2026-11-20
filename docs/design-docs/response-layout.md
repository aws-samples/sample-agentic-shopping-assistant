# Response layout

Status: verified

This is how the chat panel lays out one of Aria's answers, and why: prose with inline product citations, a product card wherever the agent cites a product on a line of its own, a note when the answer ended early, and no other tool output. [FRONTEND.md](../FRONTEND.md) covers the mechanics around it (the chat store, stream parsing, cancellation, history loading), and [DESIGN.md](../DESIGN.md) the visual specs.

The rules live in `packages/web/app/utils/citations.ts`, `utils/markdown.ts`, `utils/turns.ts`, `utils/message.ts`, `utils/stream-events.ts` and `utils/validation.ts`. They are rendered by `app/components/chat/Content.vue`, `Answer.vue` and `ProductCard.vue`. The agent's half of the contract is the `<product-recommendations>` section of [the system prompt](../../packages/agent/src/prompts/system.md).

## Goals

- **One answer per turn.** The stream splits a turn into several messages: text before a search, the tool call, its result, then more text. The shopper should read one answer, not that log.
- **Cards only for products the catalog returned.** A card needs a product from a validated `product.v1` result in this conversation, whatever id the model writes.
- **The agent places the cards.** The reason for a pick belongs next to its card, and only the model knows the reasons.
- **Untrusted text stays text.** Model output, catalog fields and tool output can't inject markup, and nothing in an answer makes the browser load or offer a URL.
- **Tool output is for the agent.** A product result changes what the shopper sees only through the cards the answer cites; other output never reaches the screen.
- **No citation syntax on screen**, even mid-stream.

## Anatomy of a turn

The prompt asks Aria to write one short sentence saying what she will look for, search, then start the answer in a new paragraph. A recommendation gives each product its own section: a heading with the reason, the citation alone on its line, and one or two sentences.

```markdown
### 출근길 보온에 맞는 자켓

[[Product:sample-1]]

겨울용 남성 캐주얼 자켓이라 추운 출근길에 가볍게 걸치기 좋아요.
```

`groupTurns()` folds everything between two shopper messages into one assistant turn, keeps stream order, and keeps only what the shopper should see:

| Message | In the transcript |
| --- | --- |
| Aria's text, not blank | Markdown, with card slots and inline citations resolved |
| A note on how the answer ended | Under the answer: "응답을 멈췄어요" (stopped), "답변이 중간에 끊겼어요" (cut off) or "답변이 길어서 여기까지만 보여 드려요" (cut for length) |
| A tool call | Hidden; it sets the [step label](../FRONTEND.md#tool-step-labels) |
| A valid `product.v1` result | Hidden; its products become citable |
| Any other tool result | Hidden, and nothing of it is kept |
| A failed tool result | Hidden |
| A blank assistant message | Hidden until text arrives (each one starts empty) |

Search results are never listed on their own: a product appears only where the answer cites it. When a turn ends with `guardrail_intervened`, the guardrail's message replaces the whole answer, including text that streamed before it.

## Product citations

A citation is `[[Product:<id>]]`, where `<id>` is 1–128 characters from `A–Z a–z 0–9 . _ : -`, copied from a search result. It resolves against `productsById()`: every product in every valid `product.v1` result among the loaded messages, with the latest result winning for a repeated id. Products from earlier turns count, and so do Gateway tools that return `product.v1`, such as the saved-items sample.

| Citation | Product returned | Not returned |
| --- | --- | --- |
| Alone on its line (spaces or tabs around it allowed) | The product card, in that place | A dashed note: "검색 결과에서 확인할 수 없는 상품이에요." (not in the search results) |
| Inside a sentence | The product's name in a chip | A dashed chip: "확인할 수 없는 상품" (unverifiable product) |

How it's built:

- `splitAnswer()` cuts the text at standalone citation lines into prose and card segments. `Answer.vue` renders a card segment as `ProductCard.vue`, or as the note when the id is unknown.
- Prose goes through `renderAnswer()` (`utils/markdown.ts`): one markdown-it instance with HTML and linkify off, the image and autolink rules disabled, and links rendered as their text. The `productCitations` inline rule runs before markdown-it's link rule and writes the chip itself, with the product name escaped, so the rendered prose is safe to pass to `v-html`.
- The card already shows the catalog fields, so the prompt tells the model not to repeat them. Its "비슷한 상품 찾기" (find similar) button asks Aria "비슷한 상품 보여줘: <product name>".

## Tool results

`toolResultMessage()` sorts each result into one of three kinds. None of them is displayed.

**Product results.** The result's text parses as JSON with `schemaVersion: "product.v1"` and a `products` array. Each entry must pass `isValidProduct()` (the ten catalog fields, with their types), and invalid entries are dropped. The message is hidden and its products feed the citations. An empty array is a search without matches.

**Failed results.** A result with `status: "error"` is marked `failed`, and so is a `product.v1` result whose `products` isn't an array or has no valid entry. `groupTurns()` drops them. The model sees the error and either corrects the call (an invalid catalog value comes back with the allowed values) or explains the failure in its answer, as the prompt asks.

**Other results.** Anything else keeps nothing. The API sends `content` only for a `product.v1` result, and the web ignores content on any other result rather than relying on that filter. Output that isn't a `product.v1` result isn't written for shoppers, and a Gateway target's JSON can hold data meant for the agent alone, so the transcript shows only what Aria writes about it. While such a tool runs, the step label reads "정보를 확인하고 있어요" (checking information), and once it returns, "답변을 정리하고 있어요" (putting the answer together).

### Limits and fallbacks

Status: accepted

- Only a `product.v1` result's text is read: the `text` of every content block, joined with newlines, as one JSON document. Other block types are ignored.
- A `product.v1` result keeps its valid products and drops the rest; one without a valid product counts as failed.
- A result over 64 KiB never reaches the browser intact: the agent's `ToolSafety` hook replaces it with an error result, which is then hidden.

## Streaming

`withoutUnfinishedCitation()` holds back a citation the model is still writing, such as `[[Product:sample-` or `[[Product:sample-1]`. While the message streams, it also holds back a trailing prefix of the opener (`[`, `[[P`). Once the turn ends, only an unfinished citation is dropped, so ordinary text such as `목록 [` stays. `Content.vue` treats only the newest message as streaming, and only while the request is open.

## Restored history

Status: accepted

History is text only: the API returns role/text pairs and drops tool-only content, so a restored conversation carries no product results. Restored assistant messages are marked `restored`. Their standalone citations render nothing, and their inline citations read "이 상품" ("this product") as plain text, without a chip. A citation still resolves normally if a search later in the same view returns its id.

## Event contract

The layout relies on these version-1 events from `POST /v1/invocations`. The agent sends them (`for_client()` in `packages/agent/src/app.py`), and the API relays them with exactly one `done` or `error` per turn (`packages/api/src/events.py`); [FRONTEND.md](../FRONTEND.md#streaming) describes framing, dispatch and errors.

| Event | What the layout reads |
| --- | --- |
| `textDelta`, `{ text }` | Appended to the current assistant message |
| `toolUse`, `{ toolUseId, name }` | `name`, for the step label only. Sent once the model's message is complete, before the tool runs |
| `toolResult`, `{ toolUseId, status, content? }` | `status` and the `text` of `content`, which the API sends only for a `product.v1` result, as one compact text block. Text after it goes into a new assistant message, so it renders after the result |
| `error`, `{ code, retryable, message }` | Ends the turn in the error dialog; nothing is laid out, and `message` isn't shown |
| `done`, `{ stopReason, message? }` | Ends the turn. `guardrail_intervened` replaces the answer with `message`, sent only when the guardrail runs in sync mode, or with a Korean default; `max_tokens` keeps the partial answer and adds a note. Without `done`, the answer gets the note "답변이 중간에 끊겼어요" |

The layout assumes that:

- events arrive in turn order; a result is matched to its call by `toolUseId` only to pick the step label;
- a `product.v1` result is the whole text of one result, as a single JSON document;
- the model writes Markdown without HTML, links or images, as the prompt asks. Raw HTML shows as escaped text, a link as its text, and an image as `!` and its alt text.

## Trade-offs

- **Model-placed cards.** Reasons sit beside their products, and card fields aren't repeated in prose. In exchange, a returned product that isn't cited is never shown, and the layout depends on the model following the prompt's format. `tests/citations.test.ts` parses the prompt's own example so the two can't drift apart silently.
- **Resolving against the whole loaded conversation.** The shopper can come back to a product from an earlier turn. Restored history has no products, so its citations degrade to text.
- **Hiding failed calls.** Retries don't clutter the answer. But if the model neither retries nor explains, the shopper sees no sign of the failure; diagnose it from logs and traces.
- **Hiding other results.** The shopper never sees raw JSON or data a tool returned for the agent. In exchange, a tool without a renderer reaches the shopper only through what Aria writes.
- **Disabling instead of sanitizing.** markdown-it with HTML, images and links off, plus a citation renderer that escapes, needs no HTML sanitizer. The price is no rich HTML, links or images in answers, which the prompt forbids anyway.
- **Per-line card detection.** Cheap and streaming-friendly: a card appears as soon as its citation line is complete. If the model went on writing on that line, the card would turn into an inline chip.

## Known gaps

- **Only products have a renderer.** Every other tool's output is dropped. Showing it would need a renderer per result schema, each with a versioned contract like `product.v1`.
- **Uncited products are invisible.** A turn that searches but cites nothing shows no cards.
- **Restored conversations have no cards.** The product results would have to be stored with the conversation.

## Verification

| Behavior | Evidence |
| --- | --- |
| Card slots, inline names, unknown inline ids, escaping of model markup and catalog strings | `packages/web/tests/citations.test.ts`: "card slots", "inline citations" |
| No images, links, autolinks or raw HTML in rendered answers | `tests/markdown.test.ts` |
| The prompt's example parses to one card for a fixture id | `tests/citations.test.ts`: "card slots" |
| Unfinished citations held back | `tests/citations.test.ts`: "streaming" |
| One answer per turn, product results not listed, failed results and output without a renderer hidden, notes kept, citations resolved only against validated results | `tests/citations.test.ts`: "turns" |
| `product.v1` parsing of the catalog fixture, invalid products dropped, failed results, nothing kept from other results; frames applied to a turn, with results matched to calls, `done`, the guardrail's replacement, `max_tokens`, malformed frames and unknown frame types | `tests/contracts.test.ts` |
| Step labels that never show a tool name | `tests/activity.test.ts` |
| Notes, the guardrail's replacement and hidden tool output through the chat store | `tests/chat.test.ts` |
| Event framing on the API side | `packages/api/tests/test_events.py` |
| Cards, notes, table wrappers, no `<img>` or `<a>`, and hidden tool output in the running app | A local headless-browser run against a mock API |
| The 64 KiB limit, restored card slots, which message counts as streaming, the note for an unknown standalone id | Code reading only (accepted) |
