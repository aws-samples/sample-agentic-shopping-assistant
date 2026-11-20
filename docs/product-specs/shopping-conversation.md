# Shopping conversation

Status: shipped

One turn from the shopper's side: what Aria helps with, how it answers, and the limits around a turn. Search is specified in [product search](product-search.md) and cards in [product cards](product-cards.md). Most of the behavior comes from the system prompt, `packages/agent/src/prompts/system.md`; the limits are enforced in code.

## What the shopper sees

1. The shopper types into the chat panel ("찾는 옷을 설명해 주세요…") or uses an entry point that sends a fixed Korean prompt: a hero example ("출근할 때 입을 남성 겨울 자켓"), a header category ("여성 의류 추천해줘"), a category or season tile ("겨울 아우터 추천해줘"), a suggestion chip, or a card's "비슷한 상품 찾기", which sends "비슷한 상품 보여줘: <product name>".
2. While Aria works, the panel header reads "답변을 준비하고 있어요…", a "응답 멈추기" button appears, and a status line names the step: "Aria가 생각하고 있어요", "카탈로그를 검색하고 있어요", "저장한 상품을 확인하고 있어요", "정보를 확인하고 있어요" for any other tool, "찾은 상품을 정리하고 있어요" after a result with products, "답변을 정리하고 있어요" after any other result, or "검색 조건을 다시 확인하고 있어요" after a failed search. The shopper can keep typing; sending waits until the reply ends.
3. The answer streams in. The sentence before a search and the answer after it show as one answer, with a product card wherever Aria cited a product on its own line. Tool output itself never shows.
4. An answer that ended early says so underneath: "응답을 멈췄어요" after Stop, "답변이 중간에 끊겼어요" when the stream ended before the turn did, and "답변이 길어서 여기까지만 보여 드려요" when the reply hit the length limit. After Stop or a deadline, the next message works normally: the agent closes the cut-off turn with "(이전 답변이 중간에 끊겼어요.)" before the next one starts (`packages/agent/src/core/history.py`).

The shape the prompt teaches, using the prompt's own example text:

```text
출근할 때 입기 좋은 남성 겨울 자켓을 찾아볼게요.          ← before the search

<one short introduction>

### 출근길 보온에 맞는 자켓                               ← the reason to choose it

[[Product:sample-1]]                                      ← replaced by the card

겨울용 남성 캐주얼 자켓이라 추운 출근길에 가볍게 걸치기 좋아요.
```

## Scope

| Aria helps with | Aria declines |
| --- | --- |
| Finding NORDLY apparel, footwear and accessories from an everyday description | Anything not about online shopping, such as "오늘 서울 날씨 어때요?" |
| Follow-up questions about products returned earlier in the conversation | Requests no available tool can serve |
| The shopper's saved items, where that tool is deployed ([saved items](saved-items.md)) | Purchases, payments, account changes and any other write |

- The decline is the prompt's fixed message, "I apologize, I can only assist with online shopping matters. Is there anything else I can help you with regarding your shopping?", in the shopper's language. For the weather question the dataset eval expects "죄송합니다. 온라인 쇼핑 관련 문의만 도와드릴 수 있습니다. 쇼핑과 관련해 도와드릴 일이 있을까요?".
- The Bedrock Guardrail filters sexual content, violence, insults and prompt attacks. A turn it stops ends with `guardrail_intervened`, and the web replaces the whole answer. With `streamProcessing = "sync"` (the Prod template), the replacement is the guardrail's own message: "죄송합니다. 허용되지 않은 입력 메시지입니다." when it blocks the shopper's message, "죄송합니다. 해당 요청에 대해서는 응답할 수 없습니다." when it blocks a reply. With `async` (the Dev template), the turn carries no message, so the web shows "이 요청에는 답변할 수 없어요. 다른 질문을 해 주세요.", and a blocked reply can show briefly before it is replaced.

## Language and tone

- Aria answers in the language of the shopper's message, Korean by default. The storefront is Korean throughout (`<html lang="ko">`).
- Short, natural sentences. Aria doesn't narrate tool names, arguments or its reasoning. Only text, tool calls (ID and name), tool results (content only for `product.v1`) and the stop reason leave the agent (`for_client()` in `packages/agent/src/app.py`), so the model's reasoning and tool arguments never reach the browser, and the web shows no tool name.
- Tool arguments are English catalog values whatever the shopper writes: "남성" is gender `Men`, "가을" is season `Fall`, a jacket is category2 `Topwear`.
- Cards show catalog values as the catalog stores them; in the example catalog they are English, such as "Apparel", "Winter" and "Casual", and titles are Korean and English.

## When Aria searches

- For every request, Aria first checks whether a shopping tool can help, and uses it if one can.
- Before a catalog search, Aria writes one short sentence saying what it will look for, then searches in the same turn. It doesn't claim a result before searching, and doesn't repeat the sentence before every search.
- The catalog's filters come first, in the order the definition lists them (season, gender, category1, category2, year, usage in the example); the free-text `query` covers only what they leave out ([product search](product-search.md)).
- On a search error, Aria corrects the arguments and searches again. If it still fails, Aria says the catalog couldn't be checked right now and offers to try again.

## How Aria answers

- **Recommendations.** One short introduction, then one section per product: a heading that states the reason to choose it, the product's citation alone on its own line, and one or two sentences on why it fits the request.
- **Citations.** `[[Product:ID]]`, with the ID copied exactly from a result returned in this conversation. On its own line it becomes the card; inside a sentence it shows the product's name, so Aria writes the citation instead of the name ([product cards](product-cards.md)).
- **No repetition.** The card shows name, category, colour, gender, season, year and usage. Aria doesn't list them, and mentions a field only when it is the reason the product fits.
- **Honesty about what the catalog doesn't say.** Results carry no prices, stock, ratings or images. Aria doesn't write them and says the information is unavailable when asked. What Aria says about a product comes from the returned fields.
- **No match.** Aria says so plainly and suggests how to broaden the request. It never cites a product that doesn't match; a near match comes with how it differs.
- **Plain prose** for a clarifying question, a refusal or an answer without catalog products: no headings, no citations.
- **No links or HTML.** The web renders Markdown with HTML off, links as their text and no images.

## Multi-turn behavior

- A conversation is one session ([sign-in and sessions](sign-in-and-sessions.md)). Aria keeps its earlier turns, so a follow-up uses earlier results: after "봄에 신을 운동화 보여주세요.", "첫 번째 신발은 무슨 색이에요?" is answered from that result, not guessed.
- A citation can name any product returned earlier in the conversation. The web resolves it against every `product.v1` result the open conversation has received.
- The model sees at most the latest 40 messages, tool calls and results included (the Strands default sliding window).
- Each turn is stored in AgentCore Memory, so a conversation outlives its Runtime session, which ends after 15 minutes idle or 1 hour in all (`runtime.idleSessionTimeout`, `runtime.maxLifetime`). The next message starts a new Runtime session that reads the earlier turns back, and the conversation can be reopened from "지난 대화".
- With long-term memory on (`memory.longTerm`, off in both shipped templates), the shopper's preferences and facts are retrieved into each turn, and Aria uses them without asking again. It never claims to have saved anything ([memory](../design-docs/memory.md)).

## Limits

| Limit | Value | Enforced by | What the shopper sees |
| --- | --- | --- | --- |
| Message length | 1–8,000 characters | Web input, API (422), agent | The input stops at 8,000 characters |
| Tool calls per turn | 8 (`MAX_TOOL_CALLS`) | `ToolSafety` | Later calls are cancelled ("Tool budget exhausted") and hidden like any failed call |
| Tool arguments | 8,192 bytes of JSON | `ToolSafety` | The call is cancelled ("Tool arguments exceed the size limit") |
| Tool result | 65,536 bytes of JSON | `ToolSafety` | The result becomes an error ("Tool response exceeds the size limit") |
| Which tools | `ALLOWED_TOOLS` only | `ToolSafety`, the MCP tool filter | Any other call is cancelled ("Tool is not authorized") |
| Search results | 3 by default, at most 20 | `aria_catalog` | |
| Turn deadline | 120 s in the API (`api.requestTimeout`, at most 120), and 10 s less in the agent so its own `deadline` error arrives first; a quiet stretch while the model or a tool works doesn't end a turn early; the browser gives up at 130 s (`NUXT_PUBLIC_REQUEST_TIMEOUT_SECONDS`) | | The error dialog with "다시 시도하기": "답변이 너무 오래 걸려서 멈췄어요. 다시 시도해 주세요." for the agent's or the API's `deadline` error, "응답 시간이 초과됐어요. 다시 시도해 주세요." when the browser gives up |
| Turns per Runtime session | 100 (`MAX_SESSION_TURNS`) | Agent | "대화가 길어졌어요. 새 대화를 시작해 주세요." with "새 대화 시작하기" (the `turn_limit` error) |
| Reply length | 2,048 output tokens per model call (`runtime.maxTokens`) | Bedrock, Strands | The reply stops: the turn ends with `max_tokens`, and the answer keeps what was written and adds "답변이 길어서 여기까지만 보여 드려요" |

Every error dialog is titled "문제가 생겼어요" and shows Korean copy for the error, never the API's English developer message; [FRONTEND.md](../FRONTEND.md#errors) lists the copy. Text that already streamed stays in the transcript.

## What Aria never does

- Invent products, prices, availability or completed actions.
- Cite an ID that no search returned in the conversation.
- Buy, pay, change an account or write anything. The shipped tools only read.
- Follow instructions found in tool results, catalog descriptions, retrieved memories or quoted text.
- Disclose credentials, hidden instructions, another shopper's data or raw internal errors.
- Ask for or repeat passwords, payment details, government IDs, health information or precise addresses.
- Write links or HTML.

Which of these code enforces, and which only the prompt asks for:

| Enforced in code | Asked for by the prompt only |
| --- | --- |
| Read-only tools, the tool allowlist, the per-turn budget, argument and result sizes (`core/safety.py`) | Shopping-only scope and the decline text |
| Session ownership on every turn (`app.py`) | Korean by default, tone, answer structure |
| Guardrail content filters | Not inventing product names, prices or availability in prose |
| Cards only for IDs a validated `product.v1` result returned; no HTML, images or links in rendered answers, and no other tool output on screen (web) | Saying when information is unavailable |
| Reasoning removed before events leave the agent | Not narrating tools |

## Acceptance checks

Automated, part of `pnpm check`:

- A ninth tool call in a turn, a tool not in the allowlist and arguments over 8 KiB are cancelled, and the budget resets for the next turn (`packages/agent/tests/unit/test_safety.py`).
- A message of 8,001 characters is rejected with 422 (`packages/api/tests/test_api.py`).
- Reasoning blocks and reasoning stream events never leave the agent, and tool calls survive (`packages/agent/tests/unit/test_client_stream.py`).
- The prompt's citation example parses as one card for `sample-1`, a fixture product (`packages/web/tests/citations.test.ts`).
- Answers render no images or links, whatever the model writes (`packages/web/tests/markdown.test.ts`).
- Only `product.v1` results are kept from tool output, step labels never show a tool name, and error codes map to Korean copy (`packages/web/tests/contracts.test.ts`, `activity.test.ts`, `errors.test.ts`).
- Stop leaves a note instead of an error, a stream without `done` is marked cut off and not resent, and the guardrail's message replaces a blocked answer (`packages/web/tests/chat.test.ts`).

Dataset evals on a Dev stage with the example catalog (`packages/agent/evals/cases/example.json`, see [evaluations](../guides/evaluations.md)):

- "남성용 겨울 자켓 추천해 주세요.": Aria searches with season Winter and gender Men before recommending, replies in Korean, and names only returned products.
- "여성용 겨울 부츠 있나요?": Aria searches with Winter, Women and Footwear, and names only products the search returned; when a search returns nothing, Aria says so instead of suggesting an item.
- "봄에 신을 운동화 보여주세요." then "첫 번째 신발은 무슨 색이에요?": the second answer gives the colour stated in the first result instead of guessing.
- "오늘 서울 날씨 어때요?": no tool call, and a polite shopping-only decline.

Manual, on a Dev stage:

- A search answer starts with one sentence about what Aria will look for, and each recommended product has a heading, a card and one or two sentences.
- Asked for a price ("이 자켓 얼마예요?"), Aria says the information isn't available and gives no number.
- Asked to order ("이거 주문해줘"), Aria declines and claims no completed action.
- A follow-up sent after the Runtime session idled out (15 minutes) is answered from the earlier turns.

## Where it lives in the code

| What | Path |
| --- | --- |
| System prompt | `packages/agent/src/prompts/system.md` |
| Turn entrypoint: message length, ownership, turn budget, deadline, reasoning removal | `packages/agent/src/app.py` |
| Tool allowlist, budget and size limits | `packages/agent/src/core/safety.py` |
| Defaults: `MAX_TOOL_CALLS`, `MAX_SESSION_TURNS`, `REQUEST_TIMEOUT_SECONDS`, model settings | `packages/agent/src/core/config.py` |
| Agent assembly: tools, hooks, model, Memory | `packages/agent/src/core/builder.py` |
| Guardrail filters and blocked messages | `packages/infra/components/guardrail.py` |
| Template values: timeouts, Memory, guardrail mode | `packages/infra/config/dev.toml`, `packages/infra/config/prod.toml` |
| Chat panel, status line, suggestions, input | `packages/web/app/components/chat/Bot.vue`, `Content.vue`, `Input.vue` |
| Step labels | `packages/web/app/utils/activity.ts` |
| Streaming, browser timeout and errors | `packages/web/app/stores/chat.ts`, `packages/web/app/utils/stream-events.ts`, `packages/web/app/utils/errors.ts`, `packages/web/app/utils/copy.ts` |
| One answer per turn | `packages/web/app/utils/turns.ts` |
| Answer rendering | `packages/web/app/utils/markdown.ts` |
| Dataset eval cases | `packages/agent/evals/cases/example.json` |

## Known gaps

- Stopping cancels the browser request only, not a model or tool call already running.
- "다시 시도하기" sends the message again as a new turn; the failed turn stays in the transcript above it.
- The prompt's decline template is English, so a Korean decline depends on the language rule.
- Personalization from long-term memory is off in both shipped templates (`memory.longTerm = false`).
