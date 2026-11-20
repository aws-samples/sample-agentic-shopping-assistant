import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { normalizeSessions, sessionTitle } from "../app/utils/sessions";

const api = vi.hoisted(() => ({ request: vi.fn(), stream: vi.fn() }));
const auth = vi.hoisted(() => ({ subject: "alice" }));
vi.mock("../app/composables/useApi", () => ({ useApi: () => api }));
vi.mock("../app/stores/auth", () => ({ useAuthStore: () => auth }));

function memoryStorage() {
  const items = new Map<string, string>();
  return {
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => void items.set(key, value),
    removeItem: (key: string) => void items.delete(key),
  };
}

beforeEach(() => {
  vi.resetAllMocks();
  auth.subject = "alice";
  vi.stubGlobal("sessionStorage", memoryStorage());
  setActivePinia(createPinia());
});

async function store() {
  return (await import("../app/stores/session")).useSessionStore();
}

describe("the open conversation", () => {
  it("survives a reload for the same shopper only", async () => {
    (await store()).setActive("alice_1");
    setActivePinia(createPinia());
    expect((await store()).persisted()).toBe("alice_1");
    auth.subject = "bob";
    expect((await store()).persisted()).toBe("");
    auth.subject = "";
    expect((await store()).persisted()).toBe("");
  });

  it("is forgotten when cleared, or when its conversation is deleted", async () => {
    const sessions = await store();
    sessions.setActive("alice_1");
    sessions.clearActive();
    expect(sessions.persisted()).toBe("");

    sessions.setActive("alice_2");
    api.request.mockResolvedValue({});
    await sessions.deleteSession("alice_2");
    expect(api.request).toHaveBeenCalledWith("/v1/sessions/alice_2", { method: "DELETE" });
    expect(sessions.sessionId).toBe("");
    expect(sessions.persisted()).toBe("");
  });

  it("is kept when another conversation is deleted", async () => {
    const sessions = await store();
    sessions.setActive("alice_1");
    api.request.mockResolvedValue({});
    await sessions.deleteSession("alice_2");
    expect(sessions.sessionId).toBe("alice_1");
    expect(sessions.persisted()).toBe("alice_1");
  });

  it("ignores storage that isn't ours", async () => {
    sessionStorage.setItem("nordly.activeSession", "{not json");
    expect((await store()).persisted()).toBe("");
    sessionStorage.setItem("nordly.activeSession", JSON.stringify({ sub: "alice", id: 7 }));
    expect((await store()).persisted()).toBe("");
  });

  it("fails loudly when the API returns no session ID", async () => {
    api.request.mockResolvedValue({});
    await expect((await store()).createSession()).rejects.toThrow();
    api.request.mockResolvedValue({ session_id: "alice_3" });
    expect(await (await store()).createSession()).toBe("alice_3");
  });
});

describe("conversation titles", () => {
  it("use the creation time when the API sends one", () => {
    const now = new Date(2026, 10, 19, 18, 0);
    const created = new Date(2026, 10, 19, 15, 5);
    expect(sessionTitle({ session_id: "a", createdAt: created.toISOString() }, 0, 1, now)).toBe("11월 19일 오후 3:05");
    expect(sessionTitle({ session_id: "a", createdAt: created.getTime() / 1000 }, 0, 1, now)).toBe("11월 19일 오후 3:05");
    expect(sessionTitle({ session_id: "a", createdAt: new Date(2025, 0, 2, 9, 30).getTime() }, 0, 1, now)).toBe("2025년 1월 2일 오전 9:30");
  });

  it("number conversations from the oldest otherwise, never by ID", () => {
    const rows = normalizeSessions([{ session_id: "same-actor_new" }, { session_id: "same-actor_old", createdAt: "not a date" }]);
    expect(rows.map((row, index) => sessionTitle(row, index, rows.length))).toEqual(["대화 2", "대화 1"]);
  });

  it("drop malformed rows", () => {
    expect(normalizeSessions([null, { session_id: "" }, { session_id: 1 }, { session_id: "a", createdAt: {} }, "x"])).toEqual([{ session_id: "a" }]);
    expect(normalizeSessions({ sessions: [] })).toEqual([]);
  });
});
