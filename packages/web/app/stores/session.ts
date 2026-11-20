import { defineStore } from "pinia";
import { ref } from "vue";
import { useApi } from "~/composables/useApi";
import { useAuthStore } from "~/stores/auth";
import { normalizeSessions, type SessionSummary } from "~/utils/sessions";

interface SessionResponse {
  session_id: string;
}

/**
 * The open conversation, kept in sessionStorage so it survives a reload and the sign-in
 * redirect. It is bound to the token's subject: another shopper signing in on the same tab
 * doesn't inherit it. UI state only; the API checks ownership of every session ID.
 */
const ACTIVE_KEY = "nordly.activeSession";

export const useSessionStore = defineStore("session", () => {
  const api = useApi();
  const authStore = useAuthStore();

  /** The conversation on screen. Set it through `setActive()` so it is persisted. */
  const sessionId = ref<string>("");

  function setActive(id: string) {
    sessionId.value = id;
    try {
      if (id && authStore.subject) sessionStorage.setItem(ACTIVE_KEY, JSON.stringify({ sub: authStore.subject, id }));
      else sessionStorage.removeItem(ACTIVE_KEY);
    } catch {
      // Storage unavailable: the conversation just won't survive a reload.
    }
  }

  function clearActive() {
    setActive("");
  }

  /** The conversation this shopper had open before a reload or a sign-in redirect, if any. */
  function persisted(): string {
    try {
      const saved: unknown = JSON.parse(sessionStorage.getItem(ACTIVE_KEY) ?? "null");
      if (typeof saved !== "object" || saved === null) return "";
      const { sub, id } = saved as Record<string, unknown>;
      return authStore.subject && sub === authStore.subject && typeof id === "string" ? id : "";
    } catch {
      return "";
    }
  }

  /** Create a conversation on the server. The caller makes it active. */
  const createSession = async () => {
    const response = await api.request<SessionResponse>("/v1/sessions", { method: "POST" });
    if (typeof response?.session_id !== "string" || !response.session_id) throw new Error("The API returned no session ID");
    return response.session_id;
  };

  const listSessions = async (): Promise<SessionSummary[]> => {
    return normalizeSessions(await api.request<unknown>("/v1/sessions"));
  };

  const deleteSession = async (sessionIdToDelete: string) => {
    await api.request(`/v1/sessions/${encodeURIComponent(sessionIdToDelete)}`, { method: "DELETE" });
    if (sessionId.value === sessionIdToDelete) clearActive();
  };

  return {
    sessionId,
    setActive,
    clearActive,
    persisted,
    createSession,
    listSessions,
    deleteSession,
  };
});
