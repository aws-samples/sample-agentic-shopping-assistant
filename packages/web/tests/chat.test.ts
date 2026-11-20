import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { COPY } from "../app/utils/copy";
import { ApiError, AuthError } from "../app/utils/errors";
import { useConcierge } from "../app/composables/useConcierge";

const api = vi.hoisted(() => ({ request: vi.fn(), stream: vi.fn() }));
const auth = vi.hoisted(() => ({ subject: "alice", authenticated: true, bearerToken: vi.fn() }));
vi.mock("../app/composables/useApi", () => ({ useApi: () => api }));
vi.mock("../app/stores/auth", () => ({ useAuthStore: () => auth }));

const config = { public: { requestTimeoutSeconds: 130 as unknown } };
const encoder = new TextEncoder();
const frame = (type: string, data?: unknown) => `data: ${JSON.stringify({ version: 1, type, data })}\n\n`;
const done = frame("done", { stopReason: "end_turn" });

/** A complete reply. */
const respond = (...frames: string[]) => () => Promise.resolve(new Response(frames.join("")));

/** A reply the test writes frame by frame. Like fetch, it fails when the request is aborted, unless told not to. */
function reply({ ignoreAbort = false } = {}) {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({ start: (c) => void (controller = c) });
  return {
    respond(_path: string, _body: unknown, options: { signal: AbortSignal }) {
      if (!ignoreAbort) options.signal.addEventListener("abort", () => controller.error(options.signal.reason));
      return Promise.resolve(new Response(body));
    },
    send: (...frames: string[]) => controller.enqueue(encoder.encode(frames.join(""))),
    end: () => controller.close(),
  };
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

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
  auth.authenticated = true;
  auth.bearerToken.mockResolvedValue("Bearer token");
  config.public.requestTimeoutSeconds = 130;
  vi.stubGlobal("useRuntimeConfig", () => config);
  vi.stubGlobal("sessionStorage", memoryStorage());
  vi.spyOn(console, "warn").mockImplementation(() => {});
  setActivePinia(createPinia());
  useConcierge().takePending();
});

afterEach(() => {
  vi.useRealTimers();
});

async function stores(sessionId = "alice_1") {
  const { useSessionStore } = await import("../app/stores/session");
  const { useChatStore } = await import("../app/stores/chat");
  const session = useSessionStore();
  if (sessionId) session.setActive(sessionId);
  return { chat: useChatStore(), session };
}

type Chat = Awaited<ReturnType<typeof stores>>["chat"];

/** The transcript as the shopper reads it: their messages, then each answer's parts. */
function transcript(chat: Chat) {
  return chat.turns.map((turn) => (turn.role === "user" ? `> ${turn.message.content}` : turn.parts.map((part) => part.content).join(" | ")));
}

describe("a turn", () => {
  it("streams an answer and ends on done", async () => {
    api.stream.mockImplementation(respond(frame("textDelta", { text: "겨울 자켓을 " }), frame("textDelta", { text: "찾았어요." }), done));
    const { chat } = await stores();
    await chat.sendMessage("겨울 자켓");
    expect(transcript(chat)).toEqual(["> 겨울 자켓", "겨울 자켓을 찾았어요."]);
    expect(chat).toMatchObject({ error: null, showErrorModal: false, isLoading: false, lastOutcome: { outcome: "done" } });
    expect(api.stream).toHaveBeenCalledWith(
      "/v1/invocations",
      { session_id: "alice_1", message: "겨울 자켓", stream: true },
      { signal: expect.any(AbortSignal) },
    );
  });

  it("renews a token that would expire before the turn's deadline", async () => {
    api.stream.mockImplementation(respond(done));
    const { chat } = await stores();
    await chat.sendMessage("q");
    expect(auth.bearerToken).toHaveBeenCalledWith(130 + 60);
  });

  it("marks an answer cut off when the stream ends without done, and doesn't resend", async () => {
    api.stream.mockImplementation(respond(frame("textDelta", { text: "찾아볼게요." })));
    const { chat } = await stores();
    await chat.sendMessage("q");
    expect(transcript(chat)).toEqual(["> q", `찾아볼게요. | ${COPY.notices.interrupted}`]);
    expect(chat).toMatchObject({ error: null, showErrorModal: false, lastOutcome: { outcome: "interrupted" } });
    expect(api.stream).toHaveBeenCalledOnce();
  });

  it("shows the guardrail's replacement instead of the answer", async () => {
    api.stream.mockImplementation(
      respond(frame("textDelta", { text: "막힐 답변" }), frame("done", { stopReason: "guardrail_intervened", message: "죄송합니다. 해당 요청에 대해서는 응답할 수 없습니다." })),
    );
    const { chat } = await stores();
    await chat.sendMessage("q");
    expect(transcript(chat)).toEqual(["> q", "죄송합니다. 해당 요청에 대해서는 응답할 수 없습니다."]);
  });

  it("notes an answer cut for length", async () => {
    api.stream.mockImplementation(respond(frame("textDelta", { text: "긴 답변" }), frame("done", { stopReason: "max_tokens" })));
    const { chat } = await stores();
    await chat.sendMessage("q");
    expect(transcript(chat)).toEqual(["> q", `긴 답변 | ${COPY.notices.maxTokens}`]);
  });

  it("shows neither tool output nor internal tool names", async () => {
    api.stream.mockImplementation(
      respond(
        frame("textDelta", { text: "확인해 볼게요." }),
        frame("toolUse", { toolUseId: "t1", name: "crm___lookup_customer" }),
        frame("toolResult", { toolUseId: "t1", status: "success", content: [{ text: '{"email":"a@example.com"}' }] }),
        frame("textDelta", { text: "확인했어요." }),
        done,
      ),
    );
    const { chat } = await stores();
    await chat.sendMessage("q");
    expect(transcript(chat)).toEqual(["> q", "확인해 볼게요. | 확인했어요."]);
    expect(JSON.stringify(chat.turns)).not.toMatch(/a@example\.com|crm___/);
  });

  it("maps an error frame to Korean copy and keeps what already streamed", async () => {
    api.stream.mockImplementation(
      respond(frame("textDelta", { text: "부분" }), frame("error", { code: "turn_limit", retryable: false, message: "Session turn budget exhausted." })),
    );
    const { chat } = await stores();
    await chat.sendMessage("q");
    expect(chat.error).toEqual({ message: "대화가 길어졌어요. 새 대화를 시작해 주세요.", action: "newConversation" });
    expect(chat.showErrorModal).toBe(true);
    expect(transcript(chat)).toEqual(["> q", "부분"]);
  });

  it("maps HTTP and network failures, never their raw text", async () => {
    const { chat } = await stores();
    for (const [failure, expected] of [
      [new ApiError(429), { message: COPY.errors.throttled, action: "retry" }],
      [new ApiError(502), { message: COPY.errors.internal, action: "retry" }],
      [new ApiError(503, "busy"), { message: COPY.errors.busy, action: "retry" }],
      [new TypeError("Failed to fetch"), { message: COPY.errors.network, action: "retry" }],
    ] as const) {
      api.stream.mockRejectedValueOnce(failure);
      await chat.sendMessage("q");
      expect(chat.error).toEqual(expected);
    }
  });

  it("reports an unreadable stream", async () => {
    api.stream.mockImplementation(respond('data: {"version":2,"type":"textDelta","data":{"text":"x"}}\n\n'));
    const { chat } = await stores();
    await chat.sendMessage("q");
    expect(chat.error).toEqual({ message: COPY.errors.unreadable });
  });

  it("takes the message back and queues it when the sign-in has expired", async () => {
    const { chat } = await stores();
    for (const failure of [() => auth.bearerToken.mockRejectedValueOnce(new AuthError("expired")), () => api.stream.mockRejectedValueOnce(new ApiError(401))]) {
      failure();
      await chat.sendMessage("겨울 자켓");
      expect(chat.messages).toEqual([]);
      expect(chat).toMatchObject({ error: null, showErrorModal: false, isLoading: false });
      expect(useConcierge().takePending()).toBe("겨울 자켓");
    }
  });

  it("asks for a new conversation when there is none", async () => {
    const { chat } = await stores("");
    await chat.sendMessage("q");
    expect(chat.error).toEqual({ message: COPY.errors.noConversation, action: "newConversation" });
    expect(api.stream).not.toHaveBeenCalled();
  });

  it("sends the latest prompt again on retry", async () => {
    const { chat } = await stores();
    api.stream.mockRejectedValueOnce(new ApiError(429)).mockImplementation(respond(frame("textDelta", { text: "됐어요." }), done));
    await chat.sendMessage("겨울 자켓");
    chat.retry();
    await settle();
    expect(api.stream).toHaveBeenCalledTimes(2);
    expect(api.stream.mock.calls[1]![1]).toMatchObject({ message: "겨울 자켓" });
    expect(chat.error).toBeNull();
  });
});

describe("stopping and switching", () => {
  it("leaves a note when the shopper stops, not the timeout dialog", async () => {
    const stream = reply();
    api.stream.mockImplementation(stream.respond);
    const { chat } = await stores();
    const sending = chat.sendMessage("q");
    await settle();
    stream.send(frame("textDelta", { text: "찾는 중" }));
    await settle();
    chat.cancel();
    await sending;
    expect(transcript(chat)).toEqual(["> q", `찾는 중 | ${COPY.notices.stopped}`]);
    expect(chat).toMatchObject({ error: null, showErrorModal: false, isLoading: false, lastOutcome: { outcome: "stopped" } });
  });

  it("keeps an answer complete when Stop comes after done", async () => {
    const stream = reply();
    api.stream.mockImplementation(stream.respond);
    const { chat } = await stores();
    const sending = chat.sendMessage("q");
    await settle();
    stream.send(frame("textDelta", { text: "다 썼어요." }), done);
    await settle();
    chat.cancel();
    await sending;
    expect(transcript(chat)).toEqual(["> q", "다 썼어요."]);
    expect(chat).toMatchObject({ error: null, showErrorModal: false, lastOutcome: { outcome: "done" } });
  });

  it("leaves the same note when the shopper stops before the reply starts", async () => {
    api.stream.mockImplementation(
      (_path: string, _body: unknown, { signal }: { signal: AbortSignal }) =>
        new Promise((_, reject) => signal.addEventListener("abort", () => reject(signal.reason))),
    );
    const { chat } = await stores();
    const sending = chat.sendMessage("q");
    await settle();
    chat.cancel();
    await sending;
    expect(transcript(chat)).toEqual(["> q", COPY.notices.stopped]);
    expect(chat.showErrorModal).toBe(false);
  });

  it("drops a superseded stream silently, including frames that arrive later", async () => {
    const stream = reply({ ignoreAbort: true });
    api.stream.mockImplementation(stream.respond);
    api.request.mockResolvedValue({ session_id: "alice_2" });
    const { chat, session } = await stores();
    const sending = chat.sendMessage("q");
    await settle();
    stream.send(frame("textDelta", { text: "예전 답변" }));
    await settle();
    await chat.startConversation();
    stream.send(frame("textDelta", { text: " 이어서" }), frame("error", { code: "internal" }));
    stream.end();
    await sending;
    expect(chat.messages).toEqual([]);
    expect(chat).toMatchObject({ error: null, showErrorModal: false, isLoading: false, lastOutcome: null });
    expect(session.sessionId).toBe("alice_2");
  });

  it("shows the timeout message only for a real timeout, from public runtime config", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    config.public.requestTimeoutSeconds = "5";
    const stream = reply();
    api.stream.mockImplementation(stream.respond);
    const { chat } = await stores();
    const sending = chat.sendMessage("q");
    await vi.advanceTimersByTimeAsync(4_999);
    expect(chat.isLoading).toBe(true);
    await vi.advanceTimersByTimeAsync(1);
    await sending;
    expect(chat.error).toEqual({ message: COPY.errors.timeout, action: "retry" });
    expect(chat.showErrorModal).toBe(true);
    expect(auth.bearerToken).toHaveBeenCalledWith(65);
  });

  it("falls back to a 130-second deadline for an unusable setting", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    config.public.requestTimeoutSeconds = "soon";
    api.stream.mockImplementation(reply().respond);
    const { chat } = await stores();
    void chat.sendMessage("q");
    await vi.advanceTimersByTimeAsync(129_000);
    expect(chat.error).toBeNull();
    await vi.advanceTimersByTimeAsync(1_000);
    expect(chat.error).toEqual({ message: COPY.errors.timeout, action: "retry" });
  });

  it("resets the error when a conversation starts or opens", async () => {
    const { chat } = await stores();
    api.stream.mockRejectedValue(new ApiError(500));
    await chat.sendMessage("q");
    chat.clearMessages();
    expect(chat).toMatchObject({ error: null, showErrorModal: false, messages: [] });

    await chat.sendMessage("q");
    api.request.mockResolvedValue([]);
    await chat.loadSessionMessages("alice_2");
    expect(chat).toMatchObject({ error: null, showErrorModal: false, messages: [] });
  });
});

describe("history", () => {
  const history = [
    { role: "user", content: "예전 질문" },
    { role: "assistant", content: "예전 답" },
    { role: "tool", content: "raw" },
    { role: "assistant", content: "  " },
  ];

  it("switches only once the history has loaded, and keeps the conversation if it can't", async () => {
    api.stream.mockImplementation(respond(frame("textDelta", { text: "답" }), done));
    const { chat, session } = await stores();
    await chat.sendMessage("q");

    api.request.mockRejectedValueOnce(new ApiError(500));
    await chat.loadSessionMessages("alice_2");
    expect(session.sessionId).toBe("alice_1");
    expect(transcript(chat)).toEqual(["> q", "답"]);
    expect(chat).toMatchObject({ historyStatus: "error", historyError: { message: COPY.history.loadFailed, action: "retry" }, isLoading: false });

    api.request.mockResolvedValueOnce(history);
    chat.retryHistory();
    await settle();
    expect(api.request).toHaveBeenLastCalledWith("/v1/sessions/alice_2/messages", { signal: expect.any(AbortSignal) });
    expect(session.sessionId).toBe("alice_2");
    expect(session.persisted()).toBe("alice_2");
    expect(transcript(chat)).toEqual(["> 예전 질문", "예전 답"]);
    expect(chat.messages[1]!.restored).toBe(true);
    expect(chat.historyStatus).toBe("idle");
  });

  it("has its own loading state, which blocks sending but isn't a streaming turn", async () => {
    let finish!: (items: unknown) => void;
    api.request.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    const { chat } = await stores();
    const loading = chat.loadSessionMessages("alice_2");
    expect(chat).toMatchObject({ historyLoading: true, isLoading: false });
    await chat.sendMessage("q");
    expect(api.stream).not.toHaveBeenCalled();
    finish([]);
    await loading;
    expect(chat.historyLoading).toBe(false);
  });

  it("ignores a load that a newer one replaced", async () => {
    let finishFirst!: (items: unknown) => void;
    api.request.mockReturnValueOnce(new Promise((resolve) => (finishFirst = resolve))).mockResolvedValueOnce([{ role: "user", content: "B" }]);
    const { chat, session } = await stores();
    const first = chat.loadSessionMessages("alice_A");
    await chat.loadSessionMessages("alice_B");
    finishFirst([{ role: "user", content: "A" }]);
    await first;
    expect(session.sessionId).toBe("alice_B");
    expect(transcript(chat)).toEqual(["> B"]);
  });

  it("stops the old conversation's stream silently when the switch happens", async () => {
    const stream = reply();
    api.stream.mockImplementation(stream.respond);
    api.request.mockResolvedValue(history);
    const { chat, session } = await stores();
    const sending = chat.sendMessage("q");
    await settle();
    await chat.loadSessionMessages("alice_2");
    await sending;
    expect(session.sessionId).toBe("alice_2");
    expect(transcript(chat)).toEqual(["> 예전 질문", "예전 답"]);
    expect(chat).toMatchObject({ error: null, showErrorModal: false, isLoading: false });
  });

  it("empties the transcript when the open conversation is deleted", async () => {
    api.stream.mockImplementation(respond(frame("textDelta", { text: "답" }), done));
    api.request.mockResolvedValue({});
    const { chat, session } = await stores();
    await chat.sendMessage("q");
    await chat.deleteConversation("alice_9");
    expect(transcript(chat)).toEqual(["> q", "답"]);
    await chat.deleteConversation("alice_1");
    expect(session.sessionId).toBe("");
    expect(session.persisted()).toBe("");
    expect(chat.messages).toEqual([]);
  });

  it("reopens the conversation from before a reload, once", async () => {
    await stores("alice_7");
    setActivePinia(createPinia());
    api.request.mockResolvedValue([{ role: "user", content: "q" }]);
    const { chat, session } = await stores("");
    chat.resumeConversation();
    chat.resumeConversation();
    await settle();
    expect(api.request).toHaveBeenCalledOnce();
    expect(session.sessionId).toBe("alice_7");
    expect(transcript(chat)).toEqual(["> q"]);
  });

  it("waits for the sign-in before reopening anything", async () => {
    await stores("alice_7");
    setActivePinia(createPinia());
    auth.authenticated = false;
    const { chat } = await stores("");
    chat.resumeConversation();
    expect(api.request).not.toHaveBeenCalled();
    auth.authenticated = true;
    api.request.mockResolvedValue([]);
    chat.resumeConversation();
    expect(api.request).toHaveBeenCalledOnce();
  });
});
