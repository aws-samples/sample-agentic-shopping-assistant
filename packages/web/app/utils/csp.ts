/**
 * The Content-Security-Policy of the app shell, set as a `<meta http-equiv>` in `nuxt.config.ts`.
 *
 * Its job is to stop exfiltration: an answer that slipped an image past the Markdown renderer
 * still couldn't make the browser fetch another origin. So images come only from the app
 * itself, `data:` URLs (the paper grain), and the origins an operator lists, and plugins and
 * `<base>` are off.
 *
 * Scripts, styles and connections are left alone on purpose. Nuxt's SPA shell boots with an
 * inline config script and an import map, Vite's dev server needs its own client, and the
 * token endpoint discovered from the OIDC issuer can be on another origin than the issuer
 * (Cognito's is). A meta policy can't set `frame-ancestors` either: do that at the web host.
 */

// An http(s) origin, optionally with a `*.` subdomain wildcard, a port and a path.
const HOST_SOURCE = /^https?:\/\/(\*\.)?[a-z0-9-]+(\.[a-z0-9-]+)*(:\d{1,5})?(\/[a-z0-9._~!$&()*+=:@%/-]*)?$/i;

/**
 * Parse extra image origins: whitespace- or comma-separated, such as
 * `https://images.example.com https://*.cdn.example.net`.
 *
 * Anything else is refused, wildcards and bare schemes included, since `*` or `https:` would
 * allow every host and undo the policy.
 */
export function imageSources(value: string | undefined): string[] {
  const sources = (value ?? "").split(/[\s,]+/).filter(Boolean);
  for (const source of sources) {
    if (!HOST_SOURCE.test(source)) {
      throw new Error(`NUXT_PUBLIC_CSP_IMG_SRC: "${source}" is not an http(s) origin such as https://images.example.com`);
    }
  }
  return sources;
}

export function contentSecurityPolicy(extraImageSources?: string): string {
  return [["img-src", "'self'", "data:", ...imageSources(extraImageSources)].join(" "), "object-src 'none'", "base-uri 'none'"].join("; ");
}
