import { UserManager, WebStorageStateStore, type User } from "oidc-client-ts";
import { defineStore } from "pinia";
import { computed, ref, shallowRef } from "vue";
import { AuthConfigError, AuthError } from "~/utils/errors";

/** Seconds left on the access token; a token without an expiry counts as none left. */
const secondsLeft = (user: User | null) => user?.expires_in ?? 0;

export const useAuthStore = defineStore("auth", () => {
  const config = useRuntimeConfig();
  const user = shallowRef<User | null>(null);
  /**
   * The shopper was signed in, but the access token expired without a renewal, or the API
   * rejected it. The chat keeps the conversation on screen and offers a new sign-in.
   */
  const expired = ref(false);
  let manager: UserManager | undefined;
  let renewal: Promise<boolean> | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | undefined;
  let retries = 0;

  function client() {
    if (!manager) {
      if (!config.public.oidcIssuer || !config.public.oidcClientId) throw new AuthConfigError();
      manager = new UserManager({
        authority: config.public.oidcIssuer,
        client_id: config.public.oidcClientId,
        redirect_uri: config.public.oidcRedirectUri || `${location.origin}/auth/callback`,
        post_logout_redirect_uri: config.public.oidcLogoutUri || `${location.origin}/`,
        response_type: "code", scope: config.public.oidcScopes,
        automaticSilentRenew: true, loadUserInfo: false,
        userStore: new WebStorageStateStore({ store: sessionStorage }),
        stateStore: new WebStorageStateStore({ store: sessionStorage }),
      });
      manager.events.addUserLoaded((next) => { user.value = next; expired.value = false; stopRetrying(); });
      manager.events.addUserUnloaded(() => { user.value = null; stopRetrying(); });
      // A failed background renewal (often a brief network loss) keeps the shopper signed in
      // while the token is still valid; renewal is retried until it succeeds or the token expires.
      manager.events.addSilentRenewError(() => { retryRenewal(); });
      manager.events.addAccessTokenExpired(async () => {
        if (!(await refresh()) && user.value?.expired) expired.value = true;
      });
    }
    return manager;
  }

  function stopRetrying() {
    clearTimeout(retryTimer);
    retryTimer = undefined;
    retries = 0;
  }

  function retryRenewal() {
    clearTimeout(retryTimer);
    const left = secondsLeft(user.value);
    if (!user.value || left <= 1) return; // The expiry event takes over.
    const delay = Math.min(5 * 2 ** retries++, 60, left - 1);
    retryTimer = setTimeout(async () => {
      if (!(await refresh())) retryRenewal();
    }, delay * 1000);
  }

  async function renew(): Promise<boolean> {
    try {
      const next = await client().signinSilent();
      if (!next) return false;
      user.value = next;
      expired.value = false;
      return !next.expired;
    } catch {
      return false; // Keep the user: callers decide whether its token is still usable.
    }
  }

  /** Renew the token. Concurrent callers share one renewal. */
  function refresh(): Promise<boolean> {
    renewal ??= renew().finally(() => { renewal = null; });
    return renewal;
  }

  async function restore() {
    user.value = await client().getUser();
    if (user.value?.expired && !(await refresh())) expired.value = true;
  }

  async function login() {
    // Cognito managed login renders in the storefront's language.
    await client().signinRedirect({ extraQueryParams: { lang: "ko" } });
  }

  async function callback() {
    user.value = await client().signinRedirectCallback();
    expired.value = false;
  }

  async function logout() {
    const current = client();
    user.value = null;
    expired.value = false;
    stopRetrying();
    if (config.public.authProvider === "cognito") {
      // Cognito uses logout_uri and does not advertise an OIDC end_session_endpoint.
      current.stopSilentRenew();
      await current.removeUser();
      const endpoint = await current.metadataService.getAuthorizationEndpoint();
      const logoutUrl = new URL("/logout", endpoint);
      logoutUrl.searchParams.set("client_id", config.public.oidcClientId);
      logoutUrl.searchParams.set("logout_uri", config.public.oidcLogoutUri || `${location.origin}/`);
      location.assign(logoutUrl.toString());
      return;
    }
    await current.signoutRedirect();
  }

  /** The API rejected the token (401): only a new sign-in, or a renewal, helps. */
  function markExpired() {
    if (user.value) expired.value = true;
  }

  /**
   * `Bearer <access token>`, never the ID token. A token with less than `minValiditySeconds`
   * left is renewed first; if renewal fails, a token that is still valid is used anyway.
   */
  async function bearerToken(minValiditySeconds = 60): Promise<string> {
    if (!user.value) await restore();
    if (!user.value) throw new AuthError("signedOut");
    if (expired.value || secondsLeft(user.value) < minValiditySeconds) await refresh();
    const current = user.value;
    if (!current) throw new AuthError("signedOut");
    if (current.expired || expired.value) {
      expired.value = true;
      throw new AuthError("expired");
    }
    return `Bearer ${current.access_token}`;
  }

  return {
    /** The token's subject, only to bind UI state such as the open conversation to this shopper. */
    subject: computed(() => user.value?.profile?.sub ?? ""),
    authenticated: computed(() => !!user.value && !user.value.expired && !expired.value),
    expired,
    login, logout, callback, restore, refresh, bearerToken, markExpired,
  };
});
