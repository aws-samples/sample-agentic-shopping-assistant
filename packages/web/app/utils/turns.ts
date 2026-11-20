import type { Product } from "~/types/domain";
import type { Message } from "~/types/dto";

/**
 * One exchange as the transcript shows it.
 *
 * The stream splits an assistant turn into several messages: text before a search, the tool
 * call, its result, then more text. The transcript shows the whole turn as one answer, with
 * its product cards placed where the agent cited them, and the notes on how it ended.
 *
 * Tool calls and results are never shown. A product result only makes its products citable;
 * a failed call is left to the agent, which corrects it or explains; and any other output is
 * not written for shoppers (it may be raw JSON from a Gateway target), so it isn't displayed.
 */
export type Turn =
  | { role: "user"; message: Message }
  | { role: "assistant"; parts: Message[] };

export function groupTurns(messages: readonly Message[]): Turn[] {
  const turns: Turn[] = [];
  for (const message of messages) {
    if (message.role === "user") {
      turns.push({ role: "user", message });
      continue;
    }
    // An assistant message is created empty and filled as text streams in.
    const shown =
      message.role === "assistant" &&
      ((message.type === "text" && message.content.trim() !== "") || message.type === "notice");
    if (!shown) continue;
    const last = turns[turns.length - 1];
    if (last?.role === "assistant") last.parts.push(message);
    else turns.push({ role: "assistant", parts: [message] });
  }
  return turns;
}

/** Every product the catalog search returned in this conversation, by id. */
export function productsById(messages: readonly Message[]): Map<string, Product> {
  const products = new Map<string, Product>();
  for (const message of messages) {
    if (message.type !== "toolResult") continue;
    for (const product of message.products ?? []) products.set(product.id, product);
  }
  return products;
}
