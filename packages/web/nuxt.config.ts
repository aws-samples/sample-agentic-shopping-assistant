// https://nuxt.com/docs/api/configuration/nuxt-config
import * as path from "path";
import tailwindcss from "@tailwindcss/vite";
import { contentSecurityPolicy } from "./app/utils/csp";

export default defineNuxtConfig({
  ssr: false,

  app: {
    head: {
      htmlAttrs: { lang: "ko" },
      link: [{ rel: "icon", type: "image/svg+xml", href: "/favicon.svg" }],
      meta: [
        // Baked into the shell at build time, so rebuild after changing NUXT_PUBLIC_CSP_IMG_SRC.
        // Lowercase so unhead places it first in <head>, before anything it governs.
        { "http-equiv": "content-security-policy", content: contentSecurityPolicy(process.env.NUXT_PUBLIC_CSP_IMG_SRC) },
        { name: "theme-color", content: "#1a211e" },
      ],
    },
  },

  compatibilityDate: "2025-08-24",

  vite: {
    plugins: [tailwindcss()],
  },

  css: ["~/assets/css/app.css", "primeicons/primeicons.css"],

  nitro: {
    output: {
      publicDir: path.join(__dirname, "dist"),
    },
  },

  runtimeConfig: {
    public: {
      apiUrl: process.env.API_URL || "",
      authProvider: process.env.AUTH_PROVIDER || "cognito",
      oidcIssuer: process.env.OIDC_ISSUER || "",
      oidcClientId: process.env.OIDC_CLIENT_ID || "",
      // aria-tools/invoke lets the agent exchange this token for its Gateway token.
      oidcScopes: process.env.OIDC_SCOPES || "openid profile email aria/chat aria-tools/invoke",
      oidcRedirectUri: process.env.OIDC_REDIRECT_URI || "",
      oidcLogoutUri: process.env.OIDC_LOGOUT_URI || "",
      brandName: process.env.BRAND_NAME || "NORDLY",
      // The browser's deadline for a whole chat turn. Keep it above the API's requestTimeout.
      requestTimeoutSeconds: Number(process.env.NUXT_PUBLIC_REQUEST_TIMEOUT_SECONDS) || 130,
    },
  },

  modules: [
    [
      "@pinia/nuxt",
      {
        autoImports: ["defineStore", "acceptHMRUpdate"],
      },
    ],
  ],

  devServer: {
    port: 4000,
  },
});
