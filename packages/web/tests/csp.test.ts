import { describe, expect, it, vi } from "vitest";
import { contentSecurityPolicy, imageSources } from "../app/utils/csp";

describe("content security policy", () => {
  it("allows images only from the app and data: URLs by default, with plugins and <base> off", () => {
    expect(contentSecurityPolicy()).toBe("img-src 'self' data:; object-src 'none'; base-uri 'none'");
    expect(contentSecurityPolicy("")).toBe(contentSecurityPolicy());
  });

  it("adds the image origins an operator lists", () => {
    expect(contentSecurityPolicy("https://images.example.com, https://*.cdn.example.net:8443/assets/")).toBe(
      "img-src 'self' data: https://images.example.com https://*.cdn.example.net:8443/assets/; object-src 'none'; base-uri 'none'",
    );
    expect(imageSources(" http://localhost:9000 \n https://img.example.com ")).toEqual(["http://localhost:9000", "https://img.example.com"]);
  });

  it("refuses sources that would allow every host or inject a directive", () => {
    for (const value of ["*", "https:", "data:", "'unsafe-inline'", "https://img.example.com;script-src", "javascript:alert(1)", "img.example.com"]) {
      expect(() => contentSecurityPolicy(value), value).toThrow(/NUXT_PUBLIC_CSP_IMG_SRC/);
    }
  });

  it("is set first in the app shell's <head>", async () => {
    vi.stubGlobal("defineNuxtConfig", (config: unknown) => config);
    vi.stubEnv("NUXT_PUBLIC_CSP_IMG_SRC", "https://images.example.com");
    vi.stubEnv("NUXT_PUBLIC_REQUEST_TIMEOUT_SECONDS", "200");
    const config = (await import("../nuxt.config")).default as any;
    expect(config.app.head.meta[0]).toEqual({
      "http-equiv": "content-security-policy",
      content: "img-src 'self' data: https://images.example.com; object-src 'none'; base-uri 'none'",
    });
    expect(config.runtimeConfig.public.requestTimeoutSeconds).toBe(200);
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });
});
