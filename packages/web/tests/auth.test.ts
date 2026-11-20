import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { AuthError } from "../app/utils/errors";

type Handler = (...args: unknown[]) => unknown;
const calls = vi.hoisted(() => ({
  removeUser: vi.fn(), stopSilentRenew: vi.fn(), getAuthorizationEndpoint: vi.fn(),
  signoutRedirect: vi.fn(), getUser: vi.fn(), signinSilent: vi.fn(), signinRedirect: vi.fn(),
  handlers: {} as Record<string, Handler>,
}));
vi.mock("oidc-client-ts", () => ({
  UserManager: class {
    events = {
      addUserLoaded: (cb: Handler) => { calls.handlers.userLoaded = cb; },
      addUserUnloaded: (cb: Handler) => { calls.handlers.userUnloaded = cb; },
      addSilentRenewError: (cb: Handler) => { calls.handlers.silentRenewError = cb; },
      addAccessTokenExpired: (cb: Handler) => { calls.handlers.accessTokenExpired = cb; },
    };
    metadataService = { getAuthorizationEndpoint: calls.getAuthorizationEndpoint };
    removeUser = calls.removeUser;
    stopSilentRenew = calls.stopSilentRenew;
    signoutRedirect = calls.signoutRedirect;
    getUser = calls.getUser;
    signinSilent = calls.signinSilent;
    signinRedirect = calls.signinRedirect;
  },
  WebStorageStateStore: class {},
}));

const config = { public: {
  authProvider: "cognito", oidcIssuer: "https://issuer.example", oidcClientId: "web",
  oidcScopes: "openid aria/chat", oidcRedirectUri: "", oidcLogoutUri: "https://shop.example/",
} };
const assign = vi.fn();

/** A stored user whose access token has `seconds` left. */
const userWith = (seconds: number, token = "access") => ({
  access_token: token, id_token: "id", expires_in: seconds, expired: seconds <= 0, profile: { sub: "alice" },
});

beforeEach(() => {
  vi.resetAllMocks();
  calls.handlers = {};
  config.public.authProvider = "cognito";
  calls.getAuthorizationEndpoint.mockResolvedValue("https://login.example/oauth2/authorize");
  vi.stubGlobal("useRuntimeConfig", () => config);
  vi.stubGlobal("sessionStorage", {});
  vi.stubGlobal("location", { origin: "https://shop.example", assign });
  setActivePinia(createPinia());
});

afterEach(() => {
  vi.useRealTimers();
});

async function store() {
  return (await import("../app/stores/auth")).useAuthStore();
}

/** A store with a signed-in shopper. */
async function signedIn(seconds: number) {
  calls.getUser.mockResolvedValue(userWith(seconds));
  const auth = await store();
  await auth.restore();
  return auth;
}

describe("logout", () => {
  it("uses Cognito's logout_uri contract and clears credentials first", async () => {
    await (await store()).logout();
    const url = new URL(assign.mock.calls[0][0]);
    expect(url.origin + url.pathname).toBe("https://login.example/logout");
    expect(Object.fromEntries(url.searchParams)).toEqual({ client_id: "web", logout_uri: "https://shop.example/" });
    expect(calls.removeUser.mock.invocationCallOrder[0]).toBeLessThan(calls.getAuthorizationEndpoint.mock.invocationCallOrder[0]);
    expect(calls.signoutRedirect).not.toHaveBeenCalled();
  });

  it("clears local credentials even when Cognito metadata is unavailable", async () => {
    calls.getAuthorizationEndpoint.mockRejectedValue(new Error("unavailable"));
    await expect((await store()).logout()).rejects.toThrow("unavailable");
    expect(calls.removeUser).toHaveBeenCalledOnce();
    expect(calls.stopSilentRenew).toHaveBeenCalledOnce();
    expect(assign).not.toHaveBeenCalled();
  });

  it("delegates BYO OIDC logout to the OIDC client's end-session flow", async () => {
    config.public.authProvider = "oidc";
    await (await store()).logout();
    expect(calls.signoutRedirect).toHaveBeenCalledOnce();
    expect(calls.getAuthorizationEndpoint).not.toHaveBeenCalled();
  });
});

describe("sign-in", () => {
  it("asks managed login for Korean", async () => {
    await (await store()).login();
    expect(calls.signinRedirect).toHaveBeenCalledWith({ extraQueryParams: { lang: "ko" } });
  });
});

describe("tokens", () => {
  it("sends the restored access token instead of the ID token", async () => {
    calls.getUser.mockResolvedValue({ access_token: "access", id_token: "id", expires_in: 3600, profile: { sub: "alice" } });
    expect(await (await store()).bearerToken()).toBe("Bearer access");
  });

  it("renews a token that would expire within the requested time", async () => {
    const auth = await signedIn(120);
    calls.signinSilent.mockResolvedValue(userWith(3600, "fresh"));
    expect(await auth.bearerToken(190)).toBe("Bearer fresh");
    expect(calls.signinSilent).toHaveBeenCalledOnce();
  });

  it("still sends a valid token when its renewal fails", async () => {
    const auth = await signedIn(120);
    calls.signinSilent.mockRejectedValue(new TypeError("Failed to fetch"));
    expect(await auth.bearerToken(190)).toBe("Bearer access");
    expect(auth.authenticated).toBe(true);
  });

  it("renews once for concurrent callers", async () => {
    const auth = await signedIn(30);
    let finish!: (value: unknown) => void;
    calls.signinSilent.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    const tokens = Promise.all([auth.bearerToken(), auth.bearerToken(), auth.refresh()]);
    finish(userWith(3600, "fresh"));
    expect(await tokens).toEqual(["Bearer fresh", "Bearer fresh", true]);
    expect(calls.signinSilent).toHaveBeenCalledOnce();
  });

  it("reports an expired sign-in instead of a signed-out shopper", async () => {
    const auth = await signedIn(3600);
    calls.getUser.mockResolvedValue(null);
    calls.signinSilent.mockRejectedValue(new Error("invalid_grant"));
    calls.handlers.userLoaded!(userWith(0));
    await expect(auth.bearerToken()).rejects.toEqual(new AuthError("expired"));
    expect(auth.expired).toBe(true);
    expect(auth.authenticated).toBe(false);
  });

  it("treats a token the API rejected as expired until a renewal succeeds", async () => {
    const auth = await signedIn(3600);
    auth.markExpired();
    expect(auth.authenticated).toBe(false);
    calls.signinSilent.mockResolvedValue(userWith(3600, "fresh"));
    expect(await auth.bearerToken()).toBe("Bearer fresh");
    expect(auth.authenticated).toBe(true);
  });
});

describe("background renewal", () => {
  it("keeps the shopper signed in after a failed renewal and retries it", async () => {
    vi.useFakeTimers();
    const auth = await signedIn(50);
    calls.signinSilent.mockRejectedValueOnce(new TypeError("Failed to fetch")).mockResolvedValueOnce(userWith(3600, "fresh"));
    calls.handlers.silentRenewError!(new TypeError("Failed to fetch"));
    expect(auth.authenticated).toBe(true);
    await vi.advanceTimersByTimeAsync(5_000);
    expect(calls.signinSilent).toHaveBeenCalledTimes(1);
    expect(auth.authenticated).toBe(true);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(calls.signinSilent).toHaveBeenCalledTimes(2);
    expect(await auth.bearerToken()).toBe("Bearer fresh");
  });

  it("shows the sign-in as expired once the token runs out unrenewed", async () => {
    const auth = await signedIn(3600);
    calls.signinSilent.mockRejectedValue(new Error("invalid_grant"));
    calls.handlers.userLoaded!(userWith(0));
    await calls.handlers.accessTokenExpired!();
    expect(auth.expired).toBe(true);
    expect(auth.authenticated).toBe(false);
  });

  it("recovers when the renewal at expiry succeeds", async () => {
    const auth = await signedIn(3600);
    calls.signinSilent.mockResolvedValue(userWith(3600, "fresh"));
    calls.handlers.userLoaded!(userWith(0));
    await calls.handlers.accessTokenExpired!();
    expect(auth.expired).toBe(false);
    expect(auth.authenticated).toBe(true);
  });
});
