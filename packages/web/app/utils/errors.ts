import { COPY } from "~/utils/copy";

/**
 * Failures, classified for the shopper.
 *
 * Each class below carries what the UI needs to pick its copy (a status, an error code) and
 * nothing it would show: `describeError()` turns any of them into Korean copy from `COPY` and,
 * where it helps, one action. An exception's own message is never displayed.
 */

export type ErrorAction = "retry" | "newConversation" | "signIn";

/** What the shopper reads when something fails. */
export interface ChatError {
  message: string;
  action?: ErrorAction;
}

/** The API answered with a non-2xx status. `code` is the body's error code, if it has one. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code?: string,
  ) {
    super(`API request failed with status ${status}`);
    this.name = "ApiError";
  }
}

/** An `error` frame ended the turn. `detail` is the API's English developer message: logged, never shown. */
export class StreamError extends Error {
  constructor(
    readonly code: string,
    readonly retryable: boolean | undefined,
    readonly detail: string,
  ) {
    super(detail || code || "Stream error");
    this.name = "StreamError";
  }
}

/** The reply couldn't be read: no body, malformed UTF-8 or JSON, or an unsupported stream version. */
export class ProtocolError extends Error {
  constructor(cause?: unknown) {
    super("The response stream could not be read", { cause });
    this.name = "ProtocolError";
  }
}

/** The connection failed while the reply was streaming. */
export class NetworkError extends Error {
  constructor(cause?: unknown) {
    super("The response stream was interrupted", { cause });
    this.name = "NetworkError";
  }
}

/** No usable access token: nobody is signed in, or the token expired and couldn't be renewed. */
export class AuthError extends Error {
  constructor(readonly reason: "signedOut" | "expired") {
    super(reason === "expired" ? "Session expired. Sign in again." : "Sign in to chat");
    this.name = "AuthError";
  }
}

/** The OIDC issuer or client ID is missing from the public configuration. */
export class AuthConfigError extends Error {
  constructor() {
    super("Identity provider is not configured");
    this.name = "AuthConfigError";
  }
}

// Error codes of the stream's `error` frame and, when the API sends one, of an HTTP error body.
const BY_CODE = new Map<string, ChatError>([
  ["turn_limit", { message: COPY.errors.turnLimit, action: "newConversation" }],
  ["owner_mismatch", { message: COPY.errors.ownerMismatch, action: "newConversation" }],
  ["busy", { message: COPY.errors.busy, action: "retry" }],
  ["throttled", { message: COPY.errors.throttled, action: "retry" }],
  ["upstream", { message: COPY.errors.upstream, action: "retry" }],
  ["internal", { message: COPY.errors.internal, action: "retry" }],
  ["init_failed", { message: COPY.errors.initFailed, action: "retry" }],
  ["deadline", { message: COPY.errors.deadline, action: "retry" }],
  ["invalid_request", { message: COPY.errors.invalidRequest }],
]);

function byStatus(status: number): ChatError {
  if (status === 408) return { message: COPY.errors.timeout, action: "retry" };
  if (status === 409) return { message: COPY.errors.busy, action: "retry" };
  if (status === 429) return { message: COPY.errors.throttled, action: "retry" };
  if (status === 400 || status === 413 || status === 422) return { message: COPY.errors.invalidRequest };
  if (status >= 500) return { message: COPY.errors.internal, action: "retry" };
  return { message: COPY.errors.generic, action: "retry" };
}

/** A failure that a new sign-in fixes: no usable token, or the API rejected the one sent. */
export function isAuthFailure(e: unknown): boolean {
  return e instanceof AuthError || (e instanceof ApiError && e.status === 401);
}

/**
 * The copy and action for a failure.
 *
 * Sign-in problems always win, because only a new sign-in fixes them. Otherwise `fallback`,
 * when given, names what failed in its context ("couldn't load the conversation"); without
 * it, the failure of a chat turn is described by its error code, its HTTP status or its kind.
 */
export function describeError(e: unknown, fallback?: ChatError): ChatError {
  if (isAuthFailure(e)) {
    const signedOut = e instanceof AuthError && e.reason === "signedOut";
    return { message: signedOut ? COPY.errors.signedOut : COPY.errors.authExpired, action: "signIn" };
  }
  if (e instanceof ApiError && e.status === 403 && e.code !== "owner_mismatch") {
    // Usually a token without a scope the API now requires; signing in again grants it.
    return { message: COPY.errors.forbidden, action: "signIn" };
  }
  if (fallback) return fallback;
  if (e instanceof StreamError) {
    return BY_CODE.get(e.code) ?? (e.retryable === false ? { message: COPY.errors.generic } : { message: COPY.errors.generic, action: "retry" });
  }
  if (e instanceof ApiError) return (e.code && BY_CODE.get(e.code)) || byStatus(e.status);
  if (e instanceof ProtocolError) return { message: COPY.errors.unreadable };
  // fetch() rejects with a TypeError when the network fails before a response.
  if (e instanceof NetworkError || e instanceof TypeError) return { message: COPY.errors.network, action: "retry" };
  return { message: COPY.errors.generic, action: "retry" };
}

/** Why the OIDC redirect callback failed, for `pages/auth/callback.vue`. */
export function callbackErrorMessage(e: unknown): string {
  if (e instanceof AuthConfigError) return COPY.auth.unavailable;
  // oidc-client-ts raises ErrorResponse for an error the IdP sent back in the redirect.
  if (e instanceof Error && e.name === "ErrorResponse") {
    return (e as Error & { error?: string | null }).error === "access_denied" ? COPY.auth.callbackCancelled : COPY.auth.callbackFailed;
  }
  // The sign-in state is gone: the callback was reopened, or sign-in started in another tab.
  if (e instanceof Error && /^No (matching )?state/.test(e.message)) return COPY.auth.callbackStale;
  if (e instanceof TypeError) return COPY.auth.callbackNetwork;
  return COPY.auth.callbackFailed;
}
