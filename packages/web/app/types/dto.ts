import type { Product } from "~/types/domain";

/** Why an answer ended early or was shortened; shown as a note under it. */
export type TurnNotice = "stopped" | "interrupted" | "maxTokens";

export interface Message {
  role: "user" | "assistant" | "tool";
  content: string;
  type?: "text" | "toolUse" | "toolResult" | "notice";
  /** A `product.v1` result's validated products. The only tool output the transcript uses. */
  products?: Product[];
  /** A tool call, or the call a result answers. Never shown: `toolName` only picks a step label. */
  toolUseId?: string;
  toolName?: string;
  /** Loaded from history. Restored turns are text-only, so their citations have no cards. */
  restored?: boolean;
  /** A tool call that failed. Not shown: the agent corrects the call or explains. */
  failed?: boolean;
  /** A note under the answer; `content` holds its copy. */
  notice?: TurnNotice;
}

/**
 * A version-1 frame of `POST /v1/invocations`. Frames are untrusted input, so every field of
 * `data` is checked where it is read: a frame may lack any of the fields below, and a field of
 * the wrong type counts as missing.
 *
 * - `textDelta`: `{ text }`
 * - `toolUse`: `{ toolUseId, name }`
 * - `toolResult`: `{ toolUseId, status: "success" | "error", content? }`; `content` is sent
 *   only for display-safe schemas (today `product.v1`)
 * - `error`: `{ code, retryable, message }`; `message` is an English developer detail
 * - `done`: `{ stopReason, message? }`, once at a normal end of turn; `message` is the
 *   guardrail's replacement text
 */
export interface StreamEvent {
  version: 1;
  type: "textDelta" | "toolUse" | "toolResult" | "error" | "done" | (string & {});
  data?: unknown;
}

/**
 * A restored conversation turn.
 *
 * AgentCore Memory stores conversational events as flat role/text pairs, so
 * restored history is text-only — tool calls are not replayed.
 */
export interface SessionMessage {
  role: "user" | "assistant";
  content: string;
}
