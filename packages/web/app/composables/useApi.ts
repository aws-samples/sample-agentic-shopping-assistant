import { ApiError } from "~/utils/errors";

/** The error code of an error body (`{"code": …}` or `{"detail": {"code": …}}`). Its text is never used. */
function errorCode(body: unknown): string | undefined {
  if (typeof body !== "object" || body === null) return undefined;
  const { code, detail } = body as Record<string, unknown>;
  if (typeof code === "string") return code;
  if (typeof detail === "object" && detail !== null) {
    const nested = (detail as Record<string, unknown>).code;
    if (typeof nested === "string") return nested;
  }
  return undefined;
}

/**
 * Authenticated API access.
 *
 * Every call attaches the shopper's access token; the API derives the shopper from it, so no
 * request names a user. A non-2xx status throws an `ApiError` with the status and the body's
 * error code, never its text.
 */
export function useApi() {
  const config = useRuntimeConfig();
  const authStore = useAuthStore();

  const url = (path: string) => `${config.public.apiUrl}${path}`;

  async function authHeaders(extra: Record<string, string> = {}) {
    return { ...extra, Authorization: await authStore.bearerToken() };
  }

  function rejected(status: number, body: unknown): ApiError {
    // The API rejected the token: the panel offers a new sign-in.
    if (status === 401) authStore.markExpired();
    return new ApiError(status, errorCode(body));
  }

  /** Typed JSON request. */
  async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const headers = await authHeaders((options.headers as Record<string, string>) ?? {});
    try {
      return await $fetch<T>(url(path), { ...options, headers } as any);
    } catch (e) {
      // $fetch's FetchError carries the status and the parsed body; without a status the request never got an answer.
      const status = (e as { status?: unknown } | null)?.status;
      if (typeof status === "number") throw rejected(status, (e as { data?: unknown }).data);
      throw e;
    }
  }

  /** Raw fetch, for streaming responses that must be read incrementally. */
  async function stream(path: string, body: unknown, options: { signal?: AbortSignal } = {}): Promise<Response> {
    const response = await fetch(url(path), {
      method: "POST",
      headers: await authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
      signal: options.signal,
    });

    if (!response.ok) throw rejected(response.status, await response.json().catch(() => undefined));
    return response;
  }

  return { request, stream };
}
