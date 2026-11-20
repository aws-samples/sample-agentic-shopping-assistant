# Sign-in and sessions

Status: shipped

How a shopper signs in, which conversations belong to them, and what happens on sign-out, token expiry and scope changes. How tokens travel behind the scenes is in [identity and tokens](../design-docs/identity-and-tokens.md); IdP settings are in [integrations](../guides/integrations.md#identity).

## What the shopper sees

- **Signed out.** The chat panel shows "안녕하세요, Aria예요." and "로그인하고 시작하기", and the header shows "로그인". A prompt asked while signed out, from the hero, a tile or a card, is shown with "로그인 후 바로 답해드릴게요." and kept across the sign-in redirect, whether the shopper signs in from the panel or from the header.
- **Signing in.** With managed Cognito, the shopper signs in on Cognito managed login with email and password. Multi-factor sign-in with an authenticator app follows `auth.mfa`. With `optional`, the default, only a shopper who has registered an authenticator is asked for a code, and managed login doesn't offer to register one; with `required`, every shopper registers one at their next sign-in. There is no self sign-up: an administrator creates shopper accounts in the user pool. The web app asks managed login for Korean (`lang=ko`). The callback page reads "로그인을 완료하는 중이에요…" and returns to the home page, replacing itself in the browser history, and a kept prompt is sent automatically. If the callback fails, it says why (the shopper cancelled, the sign-in request expired, the network failed, or "로그인을 완료하지 못했어요. 다시 로그인해 주세요.") and offers "다시 로그인하기"; if sign-in can't start, it reads "로그인을 사용할 수 없어요. 잠시 후 다시 시도해 주세요."
- **Signed in.** With no open conversation, the panel asks "무엇을 찾고 계신가요?", offers "새 대화 시작하기" and "지난 대화 보기", and notes "실제 카탈로그에 있는 상품만 추천해요."
- **Past conversations ("지난 대화").** The shopper's own conversations, most recent first, each titled by its start time ("11월 19일 오후 3:05") if the API sends one, or "대화 N" otherwise; the open one is marked "지금 대화". Choosing one restores its text, without cards, once it has loaded, and the shopper can continue it; if it can't load, the open conversation stays and the panel offers "다시 시도하기". Deleting asks "이 대화를 삭제할까요?" and warns "삭제한 대화는 되돌릴 수 없어요."; deleting the open conversation closes it. When the list, a delete or a new conversation fails, the dialog says so and lets the shopper try again. With no conversations yet, the list reads "아직 대화가 없어요." A conversation's turns are kept for 90 days by default (`memory.eventExpiryDays`).
- **Signing out.** "로그아웃" in the header.

Closing and reopening the panel keeps the open conversation. Reloading the page, or the sign-in redirect, reopens it for the same shopper and reloads its history, as text without cards.

## Sign-in rules

- OIDC authorization code with PKCE, through `oidc-client-ts`, with a public client: there is no client secret in the browser.
- The web app requests `openid profile email aria/chat aria-tools/invoke` (`OIDC_SCOPES`). With a BYO OIDC provider the names are configurable.

| Scope | Why the web app requests it |
| --- | --- |
| `aria/chat` | The API and the Runtime authorizer require it (`auth.scopes`). Without it every API call fails with 403 "Insufficient scope" |
| `aria-tools/invoke` | The agent exchanges the shopper's token for its own Gateway token, and an exchange can only narrow scopes. Without it, a session can't start on a stage with Gateway tools |
| `openid profile email` | Standard OIDC sign-in |

- The API accepts only a valid access token: signature, issuer, expiry (with 10 seconds of leeway), audience or client ID, `token_use = access` on Cognito, and the required scope. The web app never sends the ID token.
- Tokens and sign-in state live in the tab's `sessionStorage`, never in `localStorage`, so closing the tab ends the sign-in there.
- Managed Cognito lifetimes: access and ID tokens 1 hour, refresh token 30 days.

## Session ownership

- A conversation is a session. The API creates its ID from the verified token: the shopper's actor ID (the SHA-256 of the issuer, subject and optional tenant claim, 64 hex characters), `_`, and 32 random hex characters, 97 characters in all.
- Every call that names a session checks that the ID starts with the caller's actor ID: sending a message, reading history and deleting. Another shopper's session ID gets 403 "Session does not belong to this caller", and the Runtime isn't invoked.
- The agent checks again on every turn against the token the Runtime verified, including in a container it reuses for the session. A mismatch returns "Session does not belong to this caller" before the turn runs; on a session's first turn, before the agent or Memory is built.
- The session list holds only IDs bound to the caller. The caller's identity comes only from the verified token, never from a field in the request.
- The actor ID depends only on the token, so the same shopper has the same conversations on any device.
- Deleting a conversation stops its Runtime session and deletes its stored turns and summary. Saved preferences and facts are kept.

## Sign-out

- "로그아웃" stops token renewal, removes the tokens, and redirects to Cognito's `/logout` with the client ID and `logout_uri`, which returns to the home page. A BYO OIDC provider gets its own end-session redirect.
- Sign-out deletes no conversations and stops no Runtime sessions. The conversations are in "지난 대화" after the shopper signs in again.

## Token expiry

- The web app renews the access token in the background before it expires. A renewed token carries the scopes granted at sign-in.
- Before each API call, a token with less than 60 seconds left is renewed; before a chat turn, one that would expire within the turn's deadline plus 60 seconds (190 seconds by default). Concurrent calls share one renewal. If the renewal fails while the token is still valid, the call goes ahead with it.
- If background renewal fails, the shopper stays signed in while the token is valid, and the web app retries the renewal.
- When the token expires unrenewed, or the API rejects it with 401, the panel keeps the conversation on screen and shows "로그인이 만료되었어요" with "다시 로그인하기" in place of the input. A message that couldn't be sent is kept and sent after the new sign-in, in the same conversation.
- The API rejects an expired token with 401 "Invalid access token". Tokens are checked when a turn starts, so expiry doesn't cut off a reply that is already streaming.

## After a scope change

Scopes are fixed when the shopper signs in, and renewal keeps them. When a deployment changes the scopes the web app requests or a backend requires, every signed-in shopper must sign out and sign in again.

Until they do:

- Without `aria/chat`, every API call fails with 403. Starting a conversation or sending a message shows "이 대화를 이용할 권한이 없어요. 다시 로그인해 주세요." with "다시 로그인하기".
- Without `aria-tools/invoke`, on a stage with Gateway tools, the session can't be built and the shopper sees "대화를 준비하지 못했어요. 잠시 후 다시 시도해 주세요." (the `init_failed` error).

The web app takes `OIDC_SCOPES` at build time, so rebuild it when the scopes change.

## Acceptance checks

Automated, part of `pnpm check`:

- The API accepts a valid access token, and rejects a wrong issuer, an expired token, another client, an ID token, a token without `aria/chat`, an empty subject and a forged signature (`packages/api/tests/test_auth.py`).
- `POST /v1/sessions` without a token returns 401; with one, it returns a 97-character ID that starts with the caller's actor ID (`packages/api/tests/test_api.py`).
- Another shopper's session ID gets 403 on `POST /v1/invocations`, `GET /v1/sessions/{id}/messages` and `DELETE /v1/sessions/{id}`, and the Runtime isn't called (same file).
- Ownership is issuer-scoped: the same subject from another issuer doesn't own the session (`packages/api/tests/test_ownership.py`).
- The agent rejects a session owned by someone else before building the agent or reading Memory (`packages/agent/tests/unit/test_runtime_ownership.py`).
- Cognito sign-out removes local credentials before redirecting to `/logout` with `client_id` and `logout_uri`, and removes them even when the redirect can't be built (`packages/web/tests/auth.test.ts`).
- API calls carry the access token, not the ID token; managed login is asked for Korean; a failed background renewal keeps a valid token and is retried; concurrent calls renew once; an unrenewed expiry and a rejected token both count as expired, not signed out (same file).
- A message that meets an expired sign-in is taken back and queued instead of failing (`packages/web/tests/chat.test.ts`).
- The open conversation comes back after a reload for the same subject only, and is forgotten when it's deleted (`packages/web/tests/session.test.ts`).

On a Dev stage:

- After sign-in, the access token's `scope` includes `aria/chat` and `aria-tools/invoke`.
- A conversation reopened from "지난 대화", or after a reload, shows its text, and a second shopper can't open it.
- After a scope change, a shopper who signs out and in again can chat and use Gateway tools.

## Where it lives in the code

| What | Path |
| --- | --- |
| Sign-in, renewal, sign-out in the browser | `packages/web/app/stores/auth.ts` |
| Callback page, and its failure reasons | `packages/web/app/pages/auth/callback.vue`, `packages/web/app/utils/errors.ts` |
| Requested scopes and IdP settings | `packages/web/nuxt.config.ts`, `packages/web/env/local.env` |
| Bearer token on every API call | `packages/web/app/composables/useApi.ts` |
| Conversations in the browser | `packages/web/app/stores/session.ts`, `packages/web/app/stores/chat.ts`, `packages/web/app/utils/sessions.ts`, `packages/web/app/components/chat/SessionList.vue`, `packages/web/app/components/chat/Bot.vue` |
| Prompt kept across the sign-in redirect | `packages/web/app/composables/useConcierge.ts` |
| Token checks in the API | `packages/api/src/auth.py`, `packages/api/src/config.py` |
| Session routes, ownership checks, deletion | `packages/api/src/main.py`, `packages/api/src/sessions.py` |
| Actor ID and session ID rules | `packages/contracts/src/aria_contracts/identity.py` |
| Ownership check on every turn | `packages/agent/src/app.py` |
| User pool, managed login, scopes, token lifetimes | `packages/infra/stacks/auth_stack.py` |
| Scopes the Runtime authorizer requires | `packages/infra/components/identity.py` |

## Known gaps

- The API's `DELETE /v1/memory`, which deletes every conversation and saved memory, has no control in the web app.
- A 403 always offers a new sign-in. That fixes a missing scope, but not a conversation that belongs to someone else.
- When sign-out can't load Cognito's metadata, the tokens are removed but the header shows "로그인을 사용할 수 없어요. 잠시 후 다시 시도해 주세요."
