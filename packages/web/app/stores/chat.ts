import { defineStore } from "pinia";
import { computed, ref } from "vue";
import { useApi } from "~/composables/useApi";
import { useConcierge } from "~/composables/useConcierge";
import { useAuthStore } from "~/stores/auth";
import { useSessionStore } from "~/stores/session";
import type { Message, StreamEvent } from "~/types/dto";
import { COPY } from "~/utils/copy";
import { describeError, isAuthFailure, NetworkError, ProtocolError, type ChatError } from "~/utils/errors";
import { MessageBuilder, restoredMessages } from "~/utils/message";
import { SSEDecoder } from "~/utils/sse";
import { applyStreamEvent, requestTimeoutSeconds, type TurnState } from "~/utils/stream-events";
import { groupTurns, productsById } from "~/utils/turns";

/** Why a turn was aborted: the shopper pressed Stop, the deadline passed, or the conversation changed. */
type AbortReason = "user" | "timeout" | "superseded";
/** How the latest turn ended, for the one announcement after it. Errors announce themselves. */
export type TurnOutcome = "done" | "stopped" | "interrupted";
export type HistoryStatus = "idle" | "loading" | "error";

interface ActiveTurn extends TurnState {
  controller: AbortController;
}

/** Read a turn's frames until the body ends, while `current()` holds. */
async function readEvents(body: ReadableStream<Uint8Array>, current: () => boolean, onEvent: (event: StreamEvent) => void) {
  const reader = body.getReader();
  const decoder = new SSEDecoder();
  try {
    while (true) {
      let chunk: ReadableStreamReadResult<Uint8Array>;
      try {
        chunk = await reader.read();
      } catch (e) {
        throw new NetworkError(e);
      }
      // A superseded turn's frames are dropped, even those already received.
      if (!current()) return;
      let events: StreamEvent[];
      try {
        events = decoder.feed(chunk.value ?? new Uint8Array(), chunk.done);
      } catch (e) {
        throw new ProtocolError(e);
      }
      for (const event of events) onEvent(event);
      if (chunk.done) return;
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export const useChatStore = defineStore("chat", () => {
  const api = useApi();
  const auth = useAuthStore();
  const sessionStore = useSessionStore();
  const concierge = useConcierge();
  // The browser's deadline for a whole turn; the first token doesn't reset it.
  const timeoutSeconds = requestTimeoutSeconds(useRuntimeConfig().public.requestTimeoutSeconds);

  // UI 표시용 메시지
  const messages = ref<Message[]>([]);
  /** A turn is streaming. Loading history has its own state, `historyStatus`. */
  const isLoading = ref(false);
  const error = ref<ChatError | null>(null);
  const showErrorModal = ref(false);
  const lastOutcome = ref<{ outcome: TurnOutcome } | null>(null);
  const historyStatus = ref<HistoryStatus>("idle");
  const historyError = ref<ChatError | null>(null);

  // The transcript: one answer per assistant turn, with cards where the agent cited them.
  const turns = computed(() => groupTurns(messages.value));
  // Products the catalog search returned in this conversation; citations resolve against these.
  const products = computed(() => productsById(messages.value));
  const historyLoading = computed(() => historyStatus.value === "loading");

  let active: ActiveTurn | null = null;
  let lastPrompt = "";
  let historyLoad: { controller: AbortController; sessionId: string } | null = null;
  let historyTarget = "";
  let resumed = false;

  /** Stop the streaming turn. A tool already running on the server isn't cancelled. */
  function cancel(reason: AbortReason = "user") {
    active?.controller.abort(reason);
  }

  /** Abort the streaming turn silently: its conversation is leaving the screen. */
  function supersede() {
    if (!active) return;
    active.controller.abort("superseded" satisfies AbortReason);
    active = null;
    isLoading.value = false;
  }

  function showError(next: ChatError) {
    error.value = next;
    showErrorModal.value = true;
  }

  function resetErrors() {
    error.value = null;
    showErrorModal.value = false;
    lastOutcome.value = null;
  }

  async function sendMessage(content: string) {
    if (isLoading.value || historyLoading.value || content.length > 8000 || !content.trim()) return;
    if (!sessionStore.sessionId) return showError({ message: COPY.errors.noConversation, action: "newConversation" });

    const list = messages.value;
    list.push(MessageBuilder.createUserMessage(content));
    const turn: ActiveTurn = { controller: new AbortController(), start: list.length - 1, answer: -1, done: false };
    const current = () => active === turn;
    active = turn;
    lastPrompt = content;
    isLoading.value = true;
    resetErrors();
    const timer = setTimeout(() => turn.controller.abort("timeout" satisfies AbortReason), timeoutSeconds * 1000);

    try {
      // A token that would expire before the turn's deadline is renewed first.
      await auth.bearerToken(timeoutSeconds + 60);
      if (turn.controller.signal.aborted) throw turn.controller.signal.reason;
      // The body never names the shopper: the API derives identity from the access token.
      const response = await api.stream(
        "/v1/invocations",
        { session_id: sessionStore.sessionId, message: content, stream: true },
        { signal: turn.controller.signal },
      );
      if (!current()) return;
      if (!response.body) throw new ProtocolError();
      list.push(MessageBuilder.createAssistantMessage());
      turn.answer = list.length - 1;
      await readEvents(response.body, current, (event) => applyStreamEvent(list, turn, event));
      if (!current()) return;
      // A turn ends with `done`. Without it the stream was cut: say so, and don't resend.
      if (!turn.done) list.push(MessageBuilder.createNotice("interrupted"));
      lastOutcome.value = { outcome: turn.done ? "done" : "interrupted" };
    } catch (e) {
      if (!current()) return;
      if (turn.done) {
        // The answer was complete; only closing the stream failed, or Stop came after `done`.
        lastOutcome.value = { outcome: "done" };
        return;
      }
      const reason: unknown = turn.controller.signal.aborted ? turn.controller.signal.reason : undefined;
      if (reason === "user") {
        list.push(MessageBuilder.createNotice("stopped"));
        lastOutcome.value = { outcome: "stopped" };
      } else if (reason === "timeout") {
        showError({ message: COPY.errors.timeout, action: "retry" });
      } else if (isAuthFailure(e) && list.length === turn.start + 1) {
        // Nothing reached Aria. Take the message back and queue it: the panel offers a new
        // sign-in, and the message is sent once the shopper is back.
        list.splice(turn.start, 1);
        concierge.ask(content);
      } else {
        showError(describeError(e));
        console.warn("Aria turn failed", e);
      }
    } finally {
      clearTimeout(timer);
      if (current()) {
        active = null;
        isLoading.value = false;
      }
    }
  }

  /** Send the latest prompt again, as a new turn. */
  function retry() {
    if (lastPrompt) void sendMessage(lastPrompt);
  }

  function stopHistoryLoad() {
    historyLoad?.controller.abort();
    historyLoad = null;
  }

  /** Empty the transcript for a new conversation: the streaming turn and any history load stop silently. */
  function clearMessages() {
    supersede();
    stopHistoryLoad();
    messages.value = [];
    lastPrompt = "";
    resetErrors();
    historyStatus.value = "idle";
    historyError.value = null;
  }

  /** Create a conversation and open it. Throws if the API can't create one. */
  async function startConversation() {
    const id = await sessionStore.createSession();
    clearMessages();
    sessionStore.setActive(id);
  }

  /**
   * Open a past conversation. The switch happens only once its history has loaded: until
   * then, and if loading fails, the conversation on screen stays as it is. A newer load
   * cancels an older one.
   */
  async function loadSessionMessages(sessionId: string) {
    stopHistoryLoad();
    const load = { controller: new AbortController(), sessionId };
    historyLoad = load;
    historyTarget = sessionId;
    historyStatus.value = "loading";
    historyError.value = null;
    try {
      // AgentCore Memory returns flat conversational turns; tool calls are not
      // replayed, so restored history is text-only.
      const items = await api.request<unknown>(`/v1/sessions/${encodeURIComponent(sessionId)}/messages`, {
        signal: load.controller.signal,
      });
      if (historyLoad !== load) return;
      supersede();
      messages.value = restoredMessages(items);
      lastPrompt = "";
      resetErrors();
      sessionStore.setActive(sessionId);
      historyStatus.value = "idle";
    } catch (e) {
      if (historyLoad !== load) return;
      historyStatus.value = "error";
      historyError.value = describeError(e, { message: COPY.history.loadFailed, action: "retry" });
    } finally {
      if (historyLoad === load) historyLoad = null;
    }
  }

  function retryHistory() {
    if (historyTarget) void loadSessionMessages(historyTarget);
  }

  function dismissHistoryError() {
    historyStatus.value = "idle";
    historyError.value = null;
  }

  /** Reopen, once per page, the conversation the shopper had open before a reload or the sign-in redirect. */
  function resumeConversation() {
    if (resumed || !auth.authenticated) return;
    resumed = true;
    if (sessionStore.sessionId || historyStatus.value !== "idle") return;
    const id = sessionStore.persisted();
    if (id) void loadSessionMessages(id);
  }

  /** Delete a conversation; deleting the open one empties the transcript. */
  async function deleteConversation(sessionId: string) {
    const wasActive = sessionStore.sessionId === sessionId;
    await sessionStore.deleteSession(sessionId);
    if (wasActive) {
      clearMessages();
    } else if (historyTarget === sessionId && historyStatus.value !== "idle") {
      stopHistoryLoad();
      dismissHistoryError();
    }
  }

  /** Show a failure from outside a turn, such as starting a conversation. Sign-in failures show in the panel instead. */
  function reportError(e: unknown, fallback: ChatError) {
    if (!isAuthFailure(e)) showError(describeError(e, fallback));
  }

  function closeErrorModal() {
    showErrorModal.value = false;
  }

  return {
    messages,
    turns,
    products,
    isLoading,
    error,
    showErrorModal,
    lastOutcome,
    historyStatus,
    historyError,
    historyLoading,
    sendMessage,
    retry,
    cancel,
    clearMessages,
    startConversation,
    loadSessionMessages,
    retryHistory,
    dismissHistoryError,
    resumeConversation,
    deleteConversation,
    reportError,
    closeErrorModal,
  };
});
