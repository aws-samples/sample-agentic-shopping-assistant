import type { Product } from "~/types/domain";
import type { Message, TurnNotice } from "~/types/dto";
import { COPY } from "~/utils/copy";
import { isValidProduct } from "~/utils/validation";

// Message Builder
export class MessageBuilder {
  static createUserMessage(content: string): Message {
    return {
      role: "user",
      content,
      type: "text",
    };
  }

  static createAssistantMessage(content: string = ""): Message {
    return {
      role: "assistant",
      content,
      type: "text",
    };
  }

  /** A tool call. Hidden; its name only picks the step label, so it is not part of `content`. */
  static createToolUseMessage(toolName: string, toolUseId?: string): Message {
    return {
      role: "assistant",
      content: "",
      type: "toolUse",
      toolName,
      toolUseId,
    };
  }

  static createNotice(notice: TurnNotice): Message {
    return {
      role: "assistant",
      content: COPY.notices[notice],
      type: "notice",
      notice,
    };
  }
}

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null;

/**
 * The validated products of a `product.v1` result, `null` for a `product.v1` result with no
 * usable product, or `undefined` for anything else.
 *
 * The result's text blocks, joined, must be one JSON document. Invalid products are dropped
 * one by one; a result that claims `product.v1` but has no `products` array, or only invalid
 * entries, is unusable. An empty array is a search without matches, not a failure.
 */
function productResult(content: unknown): Product[] | null | undefined {
  if (!Array.isArray(content)) return undefined;
  const text = content
    .filter((block): block is { text: string } => isRecord(block) && typeof block.text === "string")
    .map((block) => block.text)
    .join("\n");
  let payload: unknown;
  try {
    payload = JSON.parse(text);
  } catch {
    return undefined;
  }
  if (!isRecord(payload) || payload.schemaVersion !== "product.v1") return undefined;
  if (!Array.isArray(payload.products)) return null;
  const valid = payload.products.filter(isValidProduct);
  return valid.length === 0 && payload.products.length > 0 ? null : valid;
}

/**
 * A tool result as the transcript keeps it. Tool output is never displayed:
 *
 * - a valid `product.v1` result keeps its products, which citations then resolve against;
 * - an error, or a `product.v1` result with no usable product, is marked `failed`;
 * - anything else (no content, or a schema without a renderer) keeps nothing.
 *
 * `toolName` is the name of the call it answers, when known, for the step label.
 */
export function toolResultMessage(data: unknown, toolName?: string): Message {
  const result = isRecord(data) ? data : {};
  const message: Message = { role: "tool", type: "toolResult", content: "" };
  if (typeof result.toolUseId === "string") message.toolUseId = result.toolUseId;
  if (toolName) message.toolName = toolName;
  if (result.status === "error") return { ...message, failed: true };
  const products = productResult(result.content);
  if (products === null) return { ...message, failed: true };
  if (products) message.products = products;
  return message;
}

/** History items as messages: blank items dropped, assistant items marked `restored`. */
export function restoredMessages(items: unknown): Message[] {
  if (!Array.isArray(items)) return [];
  return items.flatMap((item): Message[] => {
    if (!isRecord(item) || typeof item.content !== "string") return [];
    const content = item.content.trim();
    if (!content) return [];
    if (item.role === "user") return [MessageBuilder.createUserMessage(content)];
    if (item.role === "assistant") return [{ ...MessageBuilder.createAssistantMessage(content), restored: true }];
    return [];
  });
}
