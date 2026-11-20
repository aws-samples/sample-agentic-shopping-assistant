# Web app

The NORDLY storefront and Aria's chat panel: a Nuxt 4 single-page app with Vue 3, Pinia, Tailwind CSS 4 with daisyUI 5, markdown-it and oidc-client-ts. It talks only to the API.

## Commands

Run these here, or from the repository root as `pnpm --filter nordly-web <script>`:

- `pnpm dev`: dev server on http://localhost:4000. Copy `env/local.env` to `.env` and fill it in first.
- `pnpm test`: the Vitest suites in `tests/`.
- `pnpm build`: production build into `.output/` and `dist/`. `pnpm generate`: static site in `dist/`.

Install from the root with `pnpm install --frozen-lockfile`. The root `pnpm check` also runs this package's tests and build.

## Rules

- Stay a SPA (`ssr: false`). Don't add SSR, or swap Nuxt, Pinia, Tailwind/daisyUI, markdown-it or oidc-client-ts, as a side effect.
- Sign-in is OIDC code + PKCE, with tokens and state in sessionStorage. Send the access token, never the ID token. Don't add an in-app password sign-in such as Cognito SRP, keep tokens in localStorage, or send a `user_id` field: the API derives identity from the token.
- `runtimeConfig.public` ships to the browser, so it holds no secrets. It is baked in at build time: rebuild after a config change.
- HTTP calls go through `app/composables/useApi.ts`; chat and session behavior lives in `app/stores/`.
- Keep the incremental UTF-8/SSE parsing (`app/utils/sse.ts`), the `version: 1` check, reader cleanup, the per-turn deadline (`NUXT_PUBLIC_REQUEST_TIMEOUT_SECONDS`, 130 s by default) and Stop. Each turn aborts with its own reason: Stop leaves a note, a new conversation is silent, only the deadline is an error. A turn ends with `done`; without it the answer is marked cut off and never resent. Stopping doesn't cancel a tool already running on the server.
- Model and tool output is untrusted. Answers render only through `renderAnswer()` (`app/utils/markdown.ts`), with no HTML, images or links; `v-html` is only for its output; no tool output is shown except `product.v1` products. Keep the CSP meta tag (`app/utils/csp.ts`, set in `nuxt.config.ts`).
- Tool names are internal: give a tool a step label in `app/utils/activity.ts`, and never show its name.
- Copy composed in code lives in `app/utils/copy.ts`: Korean in 해요체, "대화" and never "세션". Map failures with `describeError()` (`app/utils/errors.ts`), and never show an exception's text, an error frame's `message` or an HTTP body.
- A card needs a valid `product.v1` result and a `[[Product:<id>]]` citation (`app/utils/citations.ts`). Change the format together with `packages/agent/src/prompts/system.md`; `tests/citations.test.ts` checks the prompt's example.
- Every dependency update, security patches included, needs `pnpm test` and `pnpm build`.
- Real sign-in, token renewal, logout and deployed streaming can't be checked locally; they need a separately authorized check against a deployment.

## Read more

- [docs/FRONTEND.md](../../docs/FRONTEND.md): how the app works.
- [docs/DESIGN.md](../../docs/DESIGN.md): the visual system.
- [docs/design-docs/response-layout.md](../../docs/design-docs/response-layout.md): how answers are laid out, and why.
- [docs/guides/integrations.md](../../docs/guides/integrations.md): IdP and public settings.
