import type { Message, StreamEvent } from "~/types/dto";
import { COPY } from "~/utils/copy";
import { StreamError } from "~/utils/errors";
import { MessageBuilder, toolResultMessage } from "~/utils/message";

/** Where a turn in progress stands in the message list. */
export interface TurnState {
  /** Index of the shopper's message that opened the turn. */
  start: number;
  /** Index of the assistant message that text streams into. */
  answer: number;
  /** A `done` frame arrived: the turn ended normally. */
  done: boolean;
}

/** Stop reasons after which the answer is replaced instead of shown. */
const BLOCKED = new Set(["guardrail_intervened", "content_filtered"]);

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null;
const text = (value: unknown): string => (typeof value === "string" ? value : "");

/** The tool call a result answers: by id, or the turn's latest call when the result has none. */
function toolNameFor(messages: readonly Message[], turn: TurnState, toolUseId: string): string | undefined {
  for (let i = messages.length - 1; i > turn.start; i--) {
    const message = messages[i]!;
    if (message.type === "toolUse" && (!toolUseId || message.toolUseId === toolUseId)) return message.toolName;
  }
  return undefined;
}

/**
 * Apply one decoded frame to the turn's messages. An `error` frame throws a `StreamError`.
 * Unknown frame types are ignored, and a field of the wrong type counts as missing.
 */
export function applyStreamEvent(messages: Message[], turn: TurnState, event: StreamEvent): void {
  const data = isRecord(event?.data) ? event.data : {};
  switch (event?.type) {
    case "textDelta": {
      const answer = messages[turn.answer];
      if (answer && typeof data.text === "string") answer.content += data.text;
      return;
    }
    case "toolUse":
      messages.push(MessageBuilder.createToolUseMessage(text(data.name), text(data.toolUseId) || undefined));
      return;
    case "toolResult":
      messages.push(toolResultMessage(data, toolNameFor(messages, turn, text(data.toolUseId))));
      // Text after a result is a new message, so it renders after anything the result added.
      messages.push(MessageBuilder.createAssistantMessage());
      turn.answer = messages.length - 1;
      return;
    case "error":
      throw new StreamError(text(data.code), typeof data.retryable === "boolean" ? data.retryable : undefined, text(data.message));
    case "done":
      if (turn.done) return;
      turn.done = true;
      finish(messages, turn, text(data.stopReason), text(data.message));
      return;
  }
}

function finish(messages: Message[], turn: TurnState, stopReason: string, replacement: string) {
  if (BLOCKED.has(stopReason)) {
    // The verdict covers the whole answer, including text that streamed before it.
    for (let i = turn.start + 1; i < messages.length; i++) {
      const message = messages[i]!;
      if (message.role === "assistant" && message.type === "text") message.content = "";
    }
    const content = replacement.trim() || COPY.blockedAnswer;
    const answer = messages[turn.answer];
    if (answer) answer.content = content;
    else messages.push(MessageBuilder.createAssistantMessage(content));
  } else if (stopReason === "max_tokens") {
    messages.push(MessageBuilder.createNotice("maxTokens"));
  }
}

export const DEFAULT_REQUEST_TIMEOUT_SECONDS = 130;

/** The browser's deadline for a whole turn, from `runtimeConfig.public.requestTimeoutSeconds`. */
export function requestTimeoutSeconds(value: unknown): number {
  const seconds = typeof value === "string" && value.trim() ? Number(value) : value;
  // setTimeout overflows past 2^31 ms, so cap it at an hour.
  return typeof seconds === "number" && Number.isFinite(seconds) && seconds > 0 ? Math.min(seconds, 3600) : DEFAULT_REQUEST_TIMEOUT_SECONDS;
}
