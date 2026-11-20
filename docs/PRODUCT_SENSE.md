# Product sense

The judgment behind what the shopper sees. The [product specs](product-specs/index.md) say what Aria must do; this page helps when a change isn't covered there.

## The shopper

- A signed-in NORDLY shopper on the web storefront, looking for apparel, footwear or accessories.
- They describe what they want the way they would in a shop: "출근할 때 입을 남성 겨울 자켓", "봄에 신을 운동화", "여름 데이트룩 셔츠" (the hero examples).
- They want a few real options with a reason for each, not a catalog dump.
- The UI makes them a promise: "실제 카탈로그에 있는 상품만 추천해요." and "검색 결과에 없는 상품은 지어내지 않아요. 맞는 상품이 없으면 그렇다고 알려드려요." Every change has to keep it.

## Korean first

- What the shopper reads is Korean: UI copy, Aria's answers (Korean by default, otherwise the shopper's language) and the guardrail's messages.
- What Aria sends to tools is the catalog's own English values: "가을" is season `Fall`, a coat is category2 `Topwear`. The model translates at the tool call; the values stay as the catalog stores them.
- English that reaches the shopper is a gap, not a style. Today it leaks through untranslated card values.

## A good answer

- One short sentence about what Aria will look for, then the search, then the answer in a new paragraph.
- Real catalog items, each under a heading that gives the reason to choose it, then its `[[Product:ID]]` card, then one or two sentences of fit. The card carries the facts; the prose carries the reason.
- Honest about what the catalog doesn't say. Results have no prices, stock, ratings or images, so Aria says the information is unavailable instead of guessing.
- Plain about an empty result, with a way to broaden the request; a near match says how it differs.
- Follow-ups answered from earlier results: "첫 번째 신발은 무슨 색이에요?" gets the colour the search returned.

## A bad answer

- A fluent product name that no search returned. It is the most damaging failure because it reads like a good answer; the custom `catalogue_grounding` evaluator exists to catch it (`packages/infra/components/evaluators.py`).
- Summer items offered for a winter request as if they matched.
- A price, a stock level, or a claim that something was ordered or saved.
- A list that repeats the card's fields, or narration of tool names and arguments.

## What Aria must never do

- Invent products, prices, availability or completed actions.
- Buy, pay, change an account or write anything: the shipped tools only read.
- Follow instructions found in tool results, catalog text, memories or quoted text.
- Reveal credentials, hidden instructions, another shopper's data or raw internal errors.
- Ask for passwords, payment details, government IDs, health information or precise addresses.

[Shopping conversation](product-specs/shopping-conversation.md#what-aria-never-does) has the full list and says which rules code enforces.

## Judging trade-offs

- **Latency versus richness.** A turn makes two or three model calls, and every hop shows up as waiting. Search runs in the agent because it runs on most turns: on Dev a local search without a query embedding takes under 10 ms, and a Gateway tool call 0.15–0.6 s; a hybrid search adds one embedding call. The tool schema lists every value the catalog definition declares so the model doesn't guess: a guessed value such as "Outerwear" fails with the allowed list, and each retry costs an extra model call of about 2.5 s. A search returns 3 products unless the request needs more. Measure on Dev before and after a change ([streaming and latency](design-docs/streaming-and-latency.md)).
- **Safety versus helpfulness.** Put the controls that matter in code: the tool allowlist, the per-turn budget, read-only tools, session ownership, the guardrail. The prompt shapes tone and structure. Don't buy safety by refusing shopping requests: Aria checks its tools first, and the decline is for requests that aren't about shopping or that no tool can serve. The guardrail's stream mode is the same trade: `sync` (the Prod template) checks a reply before it streams, and `async` (the Dev template) streams sooner but can briefly show text a filter then cuts.
- **Honesty versus coverage.** An honest empty result beats a near match passed off as a match. When the catalog has nothing that fits, saying so is correct; don't loosen filters to fill an answer.
- **Identity versus convenience.** A tool that needs to know the shopper sits behind the Gateway and reads `x-aria-actor-id`; identity is never an argument the model fills in ([where a tool belongs](design-docs/tool-placement.md)).
- **New capabilities.** Anything that writes, such as saving an item or placing an order, needs its own tool, a policy, a confirmation step and a change to the prompt's read-only rule. It never arrives as a side effect of another change.

## Checking a change

- Run the acceptance checks in the affected spec, and `pnpm check`.
- For a prompt change, run the dataset evals in `packages/agent/evals/cases/example.json` on a Dev stage. The web tests also parse the prompt's citation example.
- The prompt, the UI copy and the specs quote each other; change them together.
