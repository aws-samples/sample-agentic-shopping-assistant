# NORDLY web

[한국어](README.ko.md)

Nuxt 4 SPA with OIDC authorization code + PKCE, access-token renewal, versioned SSE and product cards. Answers render without HTML, images or links, and no tool output is shown apart from product cards. Agent and Memory are accessed through the API, never directly from browser AWS credentials.

See [integration settings](../../docs/guides/integrations.md) for Cognito/BYO OIDC and the public environment variables. Register `/auth/callback` at the IdP. `AUTH_PROVIDER` selects the documented deployment provider; both use the same OIDC browser implementation. Tokens and OAuth state use sessionStorage; no application client secret belongs in public configuration. [docs/FRONTEND.md](../../docs/FRONTEND.md#configuration) lists every public variable, including `NUXT_PUBLIC_REQUEST_TIMEOUT_SECONDS`, the browser's deadline for a chat turn (130 seconds by default).

```sh
pnpm install --frozen-lockfile
pnpm --filter nordly-web dev
pnpm --filter nordly-web test
pnpm --filter nordly-web build
```

Static generation bakes in public configuration, so rebuild when API/IdP settings change. The app shell carries a Content-Security-Policy meta tag that limits images to the app, `data:` URLs and the origins in `NUXT_PUBLIC_CSP_IMG_SRC`, and turns off plugins and `<base>`. Publish behind HTTPS, and set `frame-ancestors` and the rest of the policy at the web host. The stop button cancels the HTTP request; it does not promise durable revocation of a Runtime session ID. History and deletion remain scoped to the verified caller by the API.
