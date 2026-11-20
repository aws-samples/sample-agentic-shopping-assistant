# Frontend

`packages/web` is the NORDLY storefront and Aria's chat panel: a Nuxt 4 single-page app (`ssr: false`) built with Vue 3, Pinia, Tailwind CSS 4 with daisyUI 5, markdown-it and oidc-client-ts. The interface is Korean (`<html lang="ko">`). The browser talks only to the API ([packages/api](../packages/api/README.md)); it never calls the Runtime or the Gateway.

- [DESIGN.md](DESIGN.md): the visual system, its tokens and components, and accessibility.
- [Response layout](design-docs/response-layout.md): why answers are laid out as they are, and the event contract the layout relies on.
- [Integration settings](guides/integrations.md) and [getting started](guides/getting-started.md): IdP setup, and running the app against a deployment.

Paths below are relative to `packages/web/` unless they start with `packages/` or `docs/`.

## Commands

| In `packages/web` | From the repository root | What it does |
| --- | --- | --- |
| `pnpm dev` | `pnpm --filter nordly-web dev` | Dev server on http://localhost:4000 |
| `pnpm test` | `pnpm --filter nordly-web test` | `vitest run`: the suites in `tests/` |
| `pnpm build` | `pnpm --filter nordly-web build` | Production build: the Nitro server in `.output/`, client assets in `dist/` |
| `pnpm generate` | `pnpm --filter nordly-web generate` | Static site in `dist/` |
| `pnpm preview` | `pnpm --filter nordly-web preview` | Serves the production build locally |

Install once from the root with `pnpm install --frozen-lockfile`. `postinstall` runs `nuxt prepare`, which generates `.nuxt/` (types and auto-imports). Root `pnpm check` runs the Python suites, the scripts' tests and the docs check, then this package's tests and production build.

## Directory map

| Path | Contents |
| --- | --- |
| `nuxt.config.ts` | SPA mode, the Content-Security-Policy meta tag, global CSS, `runtimeConfig.public`, the Pinia module, dev port 4000, client output in `dist/` |
| `env/local.env` | Template for `.env` |
| `app/layouts/default.vue` | Page shell: skip link, site header, `<main>`, footer, the launcher and the single chat panel; applies the theme on mount |
| `app/pages/index.vue` | Storefront home: hero, categories, seasons, how it works |
| `app/pages/auth/callback.vue` | OIDC redirect target |
| `app/components/chat/` | The chat panel, `Bot.vue`, and its parts: `Header`, `Content` (the transcript), `Answer`, `ProductCard`, `Input`, `Suggestion`, `SessionList`, `ErrorModal`, `Launcher` |
| `app/components/home/`, `Header.vue`, `header/`, `Footer.vue` | Storefront sections; the site header with its logo and account button; the footer |
| `app/components/brand/Mark.vue` | The brand mark |
| `app/composables/` | `useApi` (HTTP), `useConcierge` (panel state and the queued prompt), `useMinimize`, `useTheme` |
| `app/stores/` | Pinia stores: `auth` (OIDC), `session` (the open conversation and the session calls), `chat` (messages, turns and history) |
| `app/utils/` | `sse.ts` (stream decoder), `stream-events.ts` (frames applied to a turn), `message.ts` (message builders, tool-result parsing), `markdown.ts` (the answer renderer), `citations.ts`, `turns.ts`, `activity.ts` (step labels), `copy.ts` (composed copy), `errors.ts` (error classes and their copy), `sessions.ts` (history rows and titles), `csp.ts` (the policy), `validation.ts` |
| `app/types/` | `domain.ts` (`Product`), `dto.ts` (`Message`, `StreamEvent`, `SessionMessage`) |
| `app/assets/css/app.css` | Tailwind and daisyUI setup, design tokens, global styles |
| `public/` | `favicon.svg`, `robots.txt` |
| `tests/` | Vitest suites |

Nuxt auto-imports components with their folder as a prefix (`components/chat/Bot.vue` is `<ChatBot>`, `home/Hero.vue` is `<HomeHero>`, `brand/Mark.vue` is `<BrandMark>`). It also auto-imports composables, utils, Vue APIs and, through `@pinia/nuxt`, the stores. Tests run outside Nuxt, so the stores and utils import what they use; only `useRuntimeConfig` stays a global, which the tests stub, and they mock `useApi`, oidc-client-ts or the auth store where needed.

## Configuration

Public configuration comes from environment variables, read when Nuxt loads `nuxt.config.ts`:

| Variable | `runtimeConfig.public` | Default | Used for |
| --- | --- | --- | --- |
| `API_URL` | `apiUrl` | empty: same origin | Prefix of every API path (`useApi`) |
| `AUTH_PROVIDER` | `authProvider` | `cognito` | Logout only: `cognito` uses Cognito's `/logout`; any other value uses the IdP's end-session endpoint |
| `OIDC_ISSUER` | `oidcIssuer` | empty | OIDC authority, used for discovery. Required |
| `OIDC_CLIENT_ID` | `oidcClientId` | empty | Public client ID. Required |
| `OIDC_SCOPES` | `oidcScopes` | `openid profile email aria/chat aria-tools/invoke` | Requested scopes. `aria-tools/invoke` lets the agent exchange the token for its Gateway token |
| `OIDC_REDIRECT_URI` | `oidcRedirectUri` | empty: `<origin>/auth/callback` | Sign-in redirect |
| `OIDC_LOGOUT_URI` | `oidcLogoutUri` | empty: `<origin>/` | Where the IdP returns after logout |
| `BRAND_NAME` | `brandName` | `NORDLY` | Page title and description, logo, hero, footer, chat header |
| `NUXT_PUBLIC_REQUEST_TIMEOUT_SECONDS` | `requestTimeoutSeconds` | `130` | The browser's deadline for a whole chat turn, in seconds. A missing, non-numeric or non-positive value means 130 |
| `NUXT_PUBLIC_CSP_IMG_SRC` | none: build time only | empty | Extra image origins in the [Content-Security-Policy](#content-security-policy) |

- For local work, copy `env/local.env` to `.env` and fill in `API_URL`, `OIDC_ISSUER` and `OIDC_CLIENT_ID` from the stack outputs, as [getting started](guides/getting-started.md) describes. The template already points the redirect and logout URIs at `http://localhost:4000`, the address the Cognito client and the API's default CORS origin expect. `.env` and `.env.*` are gitignored.
- Nuxt loads `.env` for `nuxt dev`, `nuxt build` and `nuxt generate`. The values are baked into the build: rebuild after changing them.
- Everything in `runtimeConfig.public` ships to the browser. Never put a secret there; the web client is a public OIDC client.

### Content-Security-Policy

`nuxt.config.ts` puts a `<meta http-equiv="content-security-policy">` first in the app shell's `<head>`. `app/utils/csp.ts` builds it:

```text
img-src 'self' data: <NUXT_PUBLIC_CSP_IMG_SRC>; object-src 'none'; base-uri 'none'
```

- Images load only from the app, from `data:` URLs (the paper grain) and from the listed origins. `NUXT_PUBLIC_CSP_IMG_SRC` takes http(s) origins separated by spaces or commas, optionally with a `*.` subdomain wildcard, a port and a path, such as `https://images.example.com`. Anything else, `*` and bare schemes such as `https:` included, fails the build.
- Scripts, styles and connections are left alone: the SPA shell boots with an inline config script and an import map, the dev server loads Vite's client, and Cognito's token endpoint isn't on the issuer's origin.
- A meta tag can't set `frame-ancestors`. Set it, and anything stricter, at the web host.

## Page shell and entry points

`app/layouts/default.vue` mounts one chat panel, `<ChatBot>` (`components/chat/Bot.vue`), for the whole app. Every entry point opens it through `useConcierge()`:

- `open()`: the header's "Aria에게 묻기" (ask Aria) button and the launcher.
- `ask(prompt)`: the header's category buttons, the hero form and its examples, the category cards, the season tiles, the empty chat's suggestions, and a product card's "비슷한 상품 찾기" (find similar), which asks "비슷한 상품 보여줘: <product name>".

`ask()` trims the prompt, caps it at 8,000 characters, opens the panel and queues the prompt in a single slot: a newer prompt replaces a queued one. `Bot.vue` watches the panel, the queued prompt, the sign-in state, `isLoading` and the history state. It sends the prompt once the panel is open, the shopper is signed in, no reply is streaming and no conversation is loading, creating a conversation first if there is none. A prompt asked during a reply is therefore sent when the reply ends.

The panel's sign-in button, the header's "로그인" and the callback page's "다시 로그인하기" all call `stashPending()`, which copies the queued prompt to sessionStorage (`nordly.pendingPrompt`) before the redirect; `Bot.vue` re-queues it on mount.

The panel has three states: signed out (a sign-in button, and the queued prompt if there is one), signed in without a conversation (start a new one or open a past one), and the conversation (`Content.vue` and `Input.vue`). When the sign-in expires with a conversation on screen, the conversation stays and a sign-in banner takes the input's place ([authentication](#authentication)). `useMinimize()` collapses the panel to a pill, and opening the panel always restores it. Closing the panel unmounts the view but not the store, so a reply keeps streaming and the conversation is still there when the panel reopens.

Opening the panel moves focus to the message input, or to the view's primary button (`data-autofocus`). Closing it returns focus to the element that opened it, or to the launcher. Esc closes the panel, except while an IME is composing a character.

The open conversation is kept in sessionStorage (`nordly.activeSession`, with the token's subject), so it survives a reload and the sign-in redirect. Once the shopper is signed in, `resumeConversation()` reopens it by loading its history, once per page and only for the same subject. A queued prompt waits for that load.

## Authentication

`app/stores/auth.ts` wraps one oidc-client-ts `UserManager`: authorization code with PKCE, `loadUserInfo: false`, automatic silent renew, and sessionStorage for both the user and the sign-in state.

1. **Restore.** The header's account button calls `restore()` on mount, which loads a stored user and renews it once if its token has expired. `authenticated` is true while a user is loaded, its token hasn't expired and the sign-in isn't marked expired.
2. **Sign in.** `login()` calls `signinRedirect({ extraQueryParams: { lang: "ko" } })`, so Cognito managed login renders in Korean. Without `OIDC_ISSUER` or `OIDC_CLIENT_ID` it throws, and the panel, the header or the callback page shows "로그인을 사용할 수 없어요. 잠시 후 다시 시도해 주세요." (sign-in unavailable).
3. **Callback.** The IdP returns to `/auth/callback`. `app/pages/auth/callback.vue` calls `callback()` (`signinRedirectCallback()`: the code exchange and storage), then navigates to `/` with `replace: true`, so Back doesn't return to the spent callback. On failure it says why and offers "다시 로그인하기": the shopper cancelled (`access_denied`), the sign-in state is gone (the callback was reopened, or sign-in started in another tab), the network failed, or the IdP isn't configured. Anything else reads "로그인을 완료하지 못했어요. 다시 로그인해 주세요."
4. **Bearer token.** `bearerToken(minValiditySeconds = 60)` returns `Bearer <access token>`, never the ID token. It restores the user if needed, and throws `AuthError("signedOut")` if there is none. With less than `minValiditySeconds` left, or once the sign-in is marked expired, it calls `refresh()` first. If the renewal fails, a token that is still valid is used anyway; an expired one marks the sign-in expired and throws `AuthError("expired")`. A chat turn asks for its deadline plus 60 seconds: 190 by default.
5. **Renewal.** `refresh()` calls `signinSilent()`, and concurrent callers share one renewal. `automaticSilentRenew` renews about a minute before the access token expires. A failed renewal keeps the shopper signed in while the token is valid: the store retries after 5 seconds, doubling the wait up to 60 seconds, until a renewal succeeds or the token expires. At expiry it tries once more, then marks the sign-in expired. A 401 from the API marks it expired too (`markExpired()`). Renewal uses the refresh token when the IdP issued one. Without one, oidc-client-ts falls back to a hidden iframe at the redirect URI, which this app doesn't support: `/auth/callback` completes redirect sign-ins (`signinRedirectCallback()`) and never answers the iframe (`signinSilentCallback()`).
6. **Expired sign-in.** With a conversation on screen, the panel keeps it and shows "로그인이 만료되었어요" (sign-in expired), "다시 로그인하면 이 대화를 이어갈 수 있어요." and "다시 로그인하기" in place of the input. Without one, the signed-out view opens with the same title. A message that couldn't go out because of the expiry is taken back and queued, so it's sent after the new sign-in, in the reopened conversation.
7. **Logout.** The header's "로그아웃" forgets the open conversation (`nordly.activeSession`) and calls `logout()`, which clears the user. With `AUTH_PROVIDER=cognito`, it stops silent renew, removes the stored user and redirects to `/logout` on the authorization endpoint's host, with `client_id` and `logout_uri`, because Cognito publishes no `end_session_endpoint`. Otherwise it calls `signoutRedirect()`.

What the browser stores:

| Storage | Key | Contents |
| --- | --- | --- |
| sessionStorage | `oidc.user:<issuer>:<client ID>` | Tokens and profile (oidc-client-ts) |
| sessionStorage | `oidc.<state ID>` | Sign-in state, including the PKCE verifier, during a redirect (oidc-client-ts) |
| sessionStorage | `nordly.pendingPrompt` | A prompt queued across the sign-in redirect |
| sessionStorage | `nordly.activeSession` | The open conversation's ID, with the token subject it belongs to |
| localStorage | `theme` | `dark` or `light` |

## API client and sessions

`app/composables/useApi.ts` is the only place that makes HTTP calls. Both helpers add `Authorization` from `bearerToken()`:

- `request<T>(path, options)` uses Nuxt's `$fetch` for JSON.
- `stream(path, body, { signal })` sends a `POST` with a JSON body through `fetch` and returns the raw `Response`.

A non-2xx status throws an `ApiError` with the status; the body's text is never used. A 401 also marks the sign-in expired.

`app/stores/session.ts` holds the open conversation, `sessionId`, and makes the session calls:

| Call | Endpoint | Notes |
| --- | --- | --- |
| `createSession()` | `POST /v1/sessions` | Returns the server-generated ID, `<64-hex actor ID>_<32-hex UUID>`; the chat store's `startConversation()` opens it |
| `listSessions()` | `GET /v1/sessions` | The caller's sessions as `{ session_id, createdAt? }` rows, malformed rows dropped |
| `deleteSession(id)` | `DELETE /v1/sessions/{id}` | Forgets the open conversation if it was this one |
| `setActive(id)`, `persisted()` | | Keep and read the open conversation in sessionStorage, for the token's subject |
| `loadSessionMessages(id)`, in the chat store | `GET /v1/sessions/{id}/messages` | Text-only history; see [history](#history) |

The browser sends no identity of its own: the API derives the shopper from the token and rejects a session that isn't theirs. [packages/api/README.md](../packages/api/README.md) documents the endpoints.

### Past conversations

`SessionList.vue` ("지난 대화") lists `listSessions()` newest first, as the API sends them. A row's title is when the conversation started, in Korean and local time ("11월 19일 오후 3:05", with the year when it isn't this year), from its `createdAt` (ISO 8601, UTC). The API leaves `createdAt` out when Memory has none, and the title is then "대화 N", numbered from the oldest row. The open conversation is marked "지금 대화", and each delete button is labelled "<title> 삭제".

Deleting the open conversation empties the transcript and forgets it. If the list fails to load, the dialog shows "지난 대화를 불러오지 못했어요. 다시 시도해 주세요." with "다시 시도하기". A failed delete keeps the confirmation open with "대화를 삭제하지 못했어요. 다시 시도해 주세요.", and a failed "새 대화 시작하기" shows "새 대화를 시작하지 못했어요. 잠시 후 다시 시도해 주세요."

## Streaming

`sendMessage(content)` in `app/stores/chat.ts`:

1. Returns without sending while a reply streams or a conversation loads, for blank input, or for input over 8,000 characters. Without a conversation it opens the error dialog with "진행 중인 대화가 없어요. 새 대화를 시작해 주세요." and a new-conversation action.
2. Appends the shopper's message, sets `isLoading`, clears the previous error, gives the turn its own `AbortController` and starts the deadline timer, `requestTimeoutSeconds` for the whole turn; the first token doesn't reset it.
3. Calls `bearerToken(requestTimeoutSeconds + 60)`, so a token that would expire before the deadline is renewed first, then `useApi().stream("/v1/invocations", { session_id, message, stream: true }, { signal })`.
4. Requires a response body, appends an empty assistant message and makes it current. It then reads the body, feeds each chunk to an `SSEDecoder` and applies each decoded frame with `applyStreamEvent()` (`app/utils/stream-events.ts`). The end of the body flushes the decoder. Once the turn is no longer current, its frames are dropped.
5. A turn ends with a `done` frame. If the body ends without one, the answer gets the note "답변이 중간에 끊겼어요" (the answer was cut off), and the message isn't resent.
6. A `finally` block cancels and releases the reader; an outer `finally` clears the timer and, if the turn is still current, `isLoading`.

`SSEDecoder` (`app/utils/sse.ts`):

- Decodes UTF-8 incrementally with `fatal: true`: a character split across chunks is fine, and malformed UTF-8 throws.
- Splits frames on a blank line (LF or CRLF) and joins a frame's `data:` lines with newlines. Other fields (`event:`, `id:`, comments) are ignored, and so are empty and `[DONE]` frames.
- Parses each frame as JSON and requires `version === 1`. It throws "Unsupported stream version" otherwise, and "Response frame is too large" past 262,144 characters.

### Stream frames

Every frame is `{"version": 1, "type": …, "data": …}`. The agent sends these frames (`for_client()` in `packages/agent/src/app.py`), `packages/contracts/src/aria_contracts/events.py` defines them, and the API relays them, ending each turn with exactly one `done` or `error` (`packages/api/src/events.py`). The web treats frames as untrusted input and checks each field where it reads it: a field of the wrong type counts as missing, and an unknown frame type is ignored.

| Event | `data` | Handling |
| --- | --- | --- |
| `textDelta` | `{ text }` | Appends the text to the current assistant message |
| `toolUse` | `{ toolUseId, name }` | Appends a hidden tool-call message with `toolName` and `toolUseId`; it drives the [step labels](#tool-step-labels). Nothing about the call is shown |
| `toolResult` | `{ toolUseId, status, content? }` | Appends `toolResultMessage()` with the name of the call it answers, matched by `toolUseId`, then a new empty assistant message, which becomes current. The API sends `content` only for a `product.v1` result, as one compact text block, and the web reads `content` only as a `product.v1` result |
| `error` | `{ code, retryable, message }` | Ends the turn in the error dialog with the code's copy ([errors](#errors)). `message` is an English developer detail: it goes to the console and is never shown |
| `done` | `{ stopReason, message? }` | Marks the turn complete; a repeat is ignored. `guardrail_intervened` replaces the turn's whole answer with `message`, which the agent sends only when the guardrail runs in sync mode, or else with "이 요청에는 답변할 수 없어요. 다른 질문을 해 주세요.". `max_tokens` keeps the partial answer and adds the note "답변이 길어서 여기까지만 보여 드려요" |

[Response layout](design-docs/response-layout.md) explains what the transcript reads from each.

### Stop, the deadline and switching conversations

Each turn has its own `AbortController`, aborted with a reason:

- **Stop** (`user`). While a reply streams, `Input.vue` shows "응답 멈추기" (stop), which calls `cancel()`. The note "응답을 멈췄어요" (the response was stopped) goes under the answer, no dialog opens, and focus returns to the input. Stopping doesn't cancel a model call or tool already running on the server.
- **The deadline** (`timeout`). The error dialog shows "응답 시간이 초과됐어요. 다시 시도해 주세요." with "다시 시도하기".
- **Another conversation** (`superseded`). Starting a new conversation (`clearMessages()`) or opening a past one aborts the turn silently, and its later frames are dropped.

Stop, the deadline or a dropped connection after `done` leaves the answer complete. `clearMessages()` also cancels a history load, empties the transcript and resets the error state.

### Errors

`describeError()` (`app/utils/errors.ts`) picks the copy for a failure from `COPY` in `app/utils/copy.ts`, the one module for copy composed in code: Korean in 해요체, with "대화" (conversation), never "세션". An exception's text, an error frame's `message` and an HTTP body are never shown.

| Failure | The shopper reads | Action |
| --- | --- | --- |
| `turn_limit` | 대화가 길어졌어요. 새 대화를 시작해 주세요. | 새 대화 시작하기 |
| `owner_mismatch` | 이 대화는 이어갈 수 없어요. 새 대화를 시작해 주세요. | 새 대화 시작하기 |
| `busy` (not counted as a turn), HTTP 409 | 이전 답변을 아직 작성하고 있어요. 잠시 후 다시 보내 주세요. | 다시 시도하기 |
| `throttled`, HTTP 429 | 지금은 요청이 많아요. 잠시 후 다시 시도해 주세요. | 다시 시도하기 |
| `upstream`: the API's HTTP or transport failure, or an agent stream that ended without a result | 답변을 만들지 못했어요. 잠시 후 다시 시도해 주세요. | 다시 시도하기 |
| `internal`, HTTP 5xx | 일시적인 문제가 생겼어요. 잠시 후 다시 시도해 주세요. | 다시 시도하기 |
| `init_failed` | 대화를 준비하지 못했어요. 잠시 후 다시 시도해 주세요. | 다시 시도하기 |
| `deadline`, from the agent or the API | 답변이 너무 오래 걸려서 멈췄어요. 다시 시도해 주세요. | 다시 시도하기 |
| `invalid_request`, HTTP 400, 413 and 422 | 메시지를 처리하지 못했어요. 내용을 바꿔서 다시 보내 주세요. | None |
| Any other code or HTTP status, or an `error` frame without a code | 답변을 받지 못했어요. 잠시 후 다시 시도해 주세요. | 다시 시도하기; none for an `error` frame with `retryable: false` |
| The browser's deadline, HTTP 408 | 응답 시간이 초과됐어요. 다시 시도해 주세요. | 다시 시도하기 |
| A network failure | 연결이 끊겼어요. 네트워크를 확인하고 다시 시도해 주세요. | 다시 시도하기 |
| An unreadable stream: no body, malformed UTF-8 or JSON, another version | 답변을 읽지 못했어요. 페이지를 새로고침한 뒤 다시 시도해 주세요. | None |
| HTTP 403 | 이 대화를 이용할 권한이 없어요. 다시 로그인해 주세요. | 다시 로그인하기 |
| HTTP 401, or no usable token | No dialog: the message is queued again and the panel shows "로그인이 만료되었어요" | 다시 로그인하기 |

The error dialog (`ErrorModal.vue`, titled "문제가 생겼어요") shows the copy with its action as the primary button and "닫기" beside it, or "확인" alone. The same copy and action stay under the transcript until the next send. "다시 시도하기" sends the latest prompt again as a new turn. The dialog follows the native `close` event, so Esc and the backdrop reset the store's flag too. A conversation that can't be created shows "대화를 시작하지 못했어요. 잠시 후 다시 시도해 주세요." in the dialog, or the 403 copy; after a 401 the panel shows the expired sign-in instead.

## Message model

`messages` in the chat store is the single source of truth; `turns` (`groupTurns()`) and `products` (`productsById()`) are computed from it. `Message` is defined in `app/types/dto.ts`:

| Kind | `role` | `type` | Other fields | Created by |
| --- | --- | --- | --- | --- |
| Shopper text | `user` | `text` | | `MessageBuilder.createUserMessage()` |
| Aria's text | `assistant` | `text` | `restored` for history | `createAssistantMessage()`, then filled by `textDelta` |
| A note on how the answer ended | `assistant` | `notice` | `notice`: `stopped`, `interrupted` or `maxTokens`; `content` holds its copy | `createNotice()` |
| Tool call | `assistant` | `toolUse` | `toolName` and `toolUseId`; `content` is empty | `createToolUseMessage()` |
| Product result | `tool` | `toolResult` | `products`: the valid ones | `toolResultMessage()`, for a `product.v1` result |
| Other result | `tool` | `toolResult` | None: nothing of the output is kept | `toolResultMessage()` |
| Failed result | `tool` | `toolResult` | `failed: true` | `toolResultMessage()`, for `status: "error"` or a `product.v1` result without a valid product |

`TurnState` (`app/utils/stream-events.ts`) holds where the turn starts, which message text streams into, and whether `done` arrived.

### History

`loadSessionMessages(id)` has its own state, `historyStatus` (`idle`, `loading` or `error`), apart from `isLoading`. While a conversation loads, the status line reads "대화를 불러오고 있어요…", sending waits, and Stop doesn't appear. It fetches `GET /v1/sessions/{id}/messages` with its own `AbortController`; a newer load cancels an older one, whose result is then ignored.

The panel switches only once the history has arrived: a streaming turn is superseded, the messages become the non-blank user and assistant items (assistant items marked `restored`), the error state resets, and the conversation becomes the open one. If loading fails, the conversation on screen stays, and `Content.vue` shows "대화를 불러오지 못했어요. 다시 시도해 주세요." with "다시 시도하기" and "닫기".

The API returns flattened role/text pairs and drops tool-only content, so history has no tool calls, cards or step labels. Consecutive assistant items still group into one turn.

## Answers, cards and citations

The transcript (`app/components/chat/Content.vue`) renders `turns`: the shopper's text in a bubble, and each assistant turn as one column of parts, ending with any note on how it ended. `Answer.vue` renders Aria's text: `splitAnswer()` separates standalone `[[Product:<id>]]` lines (card slots) from prose; prose goes through `renderAnswer()` (`app/utils/markdown.ts`); card slots render `ProductCard.vue`. No other tool output is shown. [Response layout](design-docs/response-layout.md) explains these rules and their trade-offs, and [DESIGN.md](DESIGN.md#components) gives the visual specs.

Answers are untrusted model output, and a prompt-injected answer could try to leak the conversation through a URL. So `renderAnswer()` uses one module-level markdown-it instance that lets nothing in an answer point the browser at one:

- Raw HTML and linkify are off.
- The `image` and `autolink` rules are disabled: `![x](url)` and reference-style images never become `<img>`, and `<https://…>` stays text.
- Links render as their text alone, so `[결제](url)` offers nothing to click.
- Citations render through the `productCitations` plugin, which escapes every product string.
- Tables get a `.nd-table` wrapper that scrolls inside the panel.

Its output is safe for `v-html`, the only place `v-html` is used. The [Content-Security-Policy](#content-security-policy) backs it up for images.

Keep these in step when you change them:

- The citation format in `app/utils/citations.ts` and the `<product-recommendations>` section of `packages/agent/src/prompts/system.md`. `tests/citations.test.ts` parses the prompt's example.
- `app/types/domain.ts` and `app/utils/validation.ts` with the catalog's `product.v1` fields (`PRODUCT_FIELDS` in `packages/catalog/src/aria_catalog/contract.py`). The tests render `tests/fixtures/products.json`, which holds the example catalog's first three cards; change it with them.

`Content.vue` scrolls to the bottom on every change to `turns`, including each streamed delta, and when an error appears.

## Tool step labels

While a reply streams, `Content.vue` shows a status line under the transcript: three bouncing dots and a label from `activityLabel()` (`app/utils/activity.ts`).

| Condition | Label |
| --- | --- |
| The last message is a call to `search_products` | 카탈로그를 검색하고 있어요 (searching the catalog) |
| ... to `get_saved_items` | 저장한 상품을 확인하고 있어요 (checking saved items) |
| ... to any other tool | 정보를 확인하고 있어요 (checking information) |
| The last message is a blank assistant message after a failed `search_products` result | 검색 조건을 다시 확인하고 있어요 (rechecking the search) |
| ... after a result with products | 찾은 상품을 정리하고 있어요 (arranging the products found) |
| ... after any other result, failed or not | 답변을 정리하고 있어요 (putting the answer together) |
| Otherwise | Aria가 생각하고 있어요 (Aria is thinking) |

A tool is matched by the part of its name after the last `___`, because Gateway tools are named `<target>___<tool>`. Its name is never shown. To label a new tool, add it to `TOOL_LABELS` in `app/utils/activity.ts`.

The status line is the panel's one live region (`role="status"`). It reads the step while Aria works and "대화를 불러오고 있어요…" while history loads. When a turn ends, it announces once "답변을 완료했어요" (the answer is complete), "응답을 멈췄어요" or "답변이 중간에 끊겼어요"; a failed turn speaks through its dialog instead. The transcript isn't a live region, and the answer being written has `aria-busy="true"`. While a reply streams, the chat header's subtitle also reads "답변을 준비하고 있어요…" (preparing an answer).

## Error and empty states

| Situation | What the shopper sees | Where |
| --- | --- | --- |
| Signed out | A greeting, the sign-in button, and the queued prompt if any | `Bot.vue` |
| The sign-in expired, no conversation on screen | "로그인이 만료되었어요" and "다시 로그인하기" | `Bot.vue` |
| The sign-in expired in a conversation | The conversation, with "로그인이 만료되었어요" and "다시 로그인하기" in place of the input | `Bot.vue` |
| Sign-in can't start | Inline alert "로그인을 사용할 수 없어요. 잠시 후 다시 시도해 주세요." | `Bot.vue`, `header/Account.vue`, `pages/auth/callback.vue` |
| The callback fails | Why it failed, "다시 로그인하기" and a link home | `pages/auth/callback.vue` |
| Signed in, no conversation | "새 대화 시작하기" (new conversation) and "지난 대화 보기" (past conversations) | `Bot.vue` |
| A conversation can't be created | Error dialog "대화를 시작하지 못했어요. 잠시 후 다시 시도해 주세요." | `Bot.vue` |
| Empty conversation | A prompt and two groups of suggestion chips | `Content.vue` |
| A turn fails | Error dialog with the [error copy](#errors) and its action, also kept under the transcript | `ErrorModal.vue`, `Content.vue` |
| A reply is stopped, cut off or cut for length | A note under the answer | `Content.vue` |
| History can't load | "대화를 불러오지 못했어요. 다시 시도해 주세요." with "다시 시도하기" and "닫기"; the conversation on screen stays | `Content.vue` |
| No past conversations | "아직 대화가 없어요." (no conversations yet) | `SessionList.vue` |
| The list, a delete or a new conversation fails | The errors under [past conversations](#past-conversations), each with a way to try again | `SessionList.vue` |

## Testing

`pnpm test` runs nine Vitest suites in Node, without a browser DOM. `vitest.config.ts` maps `~` to `app/` and includes `tests/**/*.test.ts`.

| Suite | Covers |
| --- | --- |
| `tests/contracts.test.ts` | `SSEDecoder` with Korean text split at every byte, CRLF and multi-line `data:`; rejection of other versions. `toolResultMessage()`: the catalog fixture as `product.v1`, invalid products dropped, a `product.v1` result without a valid product failed, an empty search kept, nothing kept from other results, errors failed, malformed frames. `applyStreamEvent()`: text around a tool result, results matched to calls, no tool name or input in displayable text, `done` once, `max_tokens`, the guardrail's replacement, `error` frames, malformed and unknown frames |
| `tests/citations.test.ts` | Card slots and inline citations through `renderAnswer()`; every fixture id; the prompt's example; holding back unfinished citations; escaping of catalog strings and model markup; the restored-history fallback; `groupTurns()` hiding failed calls and output without a renderer, and keeping notes; `productsById()` |
| `tests/markdown.test.ts` | `renderAnswer()` emits no `<img>`, `<a>` or URL attribute for inline, reference-style and nested images, links, autolinks, `javascript:` links or raw HTML; table wrappers; escaped code |
| `tests/activity.test.ts` | Step labels: known tools with or without a target prefix, no internal names, arranging only after a result with products, the search recheck only after a failed search |
| `tests/errors.test.ts` | `describeError()` for every error code, HTTP statuses, network failures and unreadable streams, sign-in failures in any context, no exception text; the callback's reasons; every `COPY` string in 해요체 without "세션"; the similar-products prompt |
| `tests/chat.test.ts` | The chat store, with `useApi` and the auth store mocked: `done`, a missing `done`, the guardrail, `max_tokens`, hidden tool output, `error` frames, HTTP and network failures, unreadable streams, an expired sign-in, retry, Stop (before and after the reply starts, and after `done`), superseded streams, the deadline from runtime config and its fallback, error resets, history switching and failure, stale loads, deleting the open conversation, reopening after a reload |
| `tests/session.test.ts` | The open conversation in sessionStorage, for the same subject only; row titles from `createdAt` or numbered; malformed rows |
| `tests/csp.test.ts` | The policy, extra origins and refused sources; the meta tag first in `nuxt.config.ts`'s head; `requestTimeoutSeconds` from the environment |
| `tests/auth.test.ts` | The Cognito logout URL and order (credentials cleared first, even when metadata fails), BYO OIDC `signoutRedirect()`, the access token rather than the ID token as bearer, `lang=ko`, renewal before the requested validity, a valid token used when renewal fails, one renewal for concurrent callers, retries after a failed background renewal, and the expired state. oidc-client-ts is mocked |

The tests read `tests/fixtures/products.json`, the `product.v1` cards of the example catalog's first three products (`sample-1` to `sample-3`), and the agent's prompt, `packages/agent/src/prompts/system.md`.

Not covered: the Vue components (layout, dialogs, focus, theme) and `useApi`. Real sign-in, token renewal, logout and deployed streaming need a separately authorized check against a deployment. [Testing](guides/testing.md) covers the rest of the repository.

## Conventions

- Stay a SPA. Don't introduce SSR, or replace Nuxt, Pinia, Tailwind/daisyUI, markdown-it or oidc-client-ts, as a side effect of other work.
- Keep tokens and OIDC state in sessionStorage, and send the access token. Don't add an in-app password sign-in such as Cognito SRP, store tokens in localStorage, or put a `user_id` field in request bodies.
- Keep HTTP calls in `useApi`, and chat and session behavior in the stores. Identity comes from the token, verified on the server, never from a client-side actor ID.
- Keep the incremental UTF-8/SSE parsing, the version check, the reader cleanup, the per-turn deadline and Stop.
- Treat model and tool output as untrusted: render answers only through `renderAnswer()`, use `v-html` only for its output, escape product strings, show no other tool output, and keep the CSP meta tag.
- Put copy composed in code in `app/utils/copy.ts`, in 해요체 and with "대화", and never show an exception's text, an error frame's `message` or an HTTP body.
- Any dependency update, security patches included, needs `pnpm test` and `pnpm build`.

## Known gaps

- `api.requestTimeout` is at most 120 seconds, so a turn ends before the browser gives up at 130, the default of `NUXT_PUBLIC_REQUEST_TIMEOUT_SECONDS`. A lower browser deadline would give up first.
- An image in an answer shows as `!` and its alt text.
- "다시 시도하기" sends the prompt as a new turn; the failed turn stays above it.
- An HTTP 403 offers a new sign-in, which fixes a missing scope but not a session that belongs to someone else.
- The transcript always scrolls to the bottom, and long histories aren't virtualized.
