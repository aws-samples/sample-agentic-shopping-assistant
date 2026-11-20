import type { Message } from "~/types/dto";
import { COPY } from "~/utils/copy";

/**
 * The status line while Aria works: what she is doing right now, in shopper terms.
 *
 * Tool names are internal (Gateway tools are `<target>___<tool>`), so they are never shown.
 * A known tool has its own label; any other reads "정보를 확인하고 있어요".
 */

const TOOL_LABELS = new Map<string, string>([
  ["search_products", COPY.steps.searching],
  ["get_saved_items", COPY.steps.savedItems],
]);

/** The tool part of a name: `saved-items___get_saved_items` → `get_saved_items`. */
export function toolBaseName(name: string | undefined): string {
  return (name ?? "").split("___").pop()!.trim();
}

export function toolLabel(name: string | undefined): string {
  return TOOL_LABELS.get(toolBaseName(name)) ?? COPY.steps.otherTool;
}

/** The label for the newest message of a turn in progress. */
export function activityLabel(messages: readonly Message[]): string {
  const last = messages[messages.length - 1];
  if (last?.type === "toolUse") return toolLabel(last.toolName);
  const previous = messages[messages.length - 2];
  // A result arrived and the next text hasn't: say what Aria does with it.
  if (last?.role === "assistant" && last.type === "text" && !last.content.trim() && previous?.role === "tool") {
    if (previous.failed) return toolBaseName(previous.toolName) === "search_products" ? COPY.steps.retrying : COPY.steps.composing;
    return previous.products?.length ? COPY.steps.arranging : COPY.steps.composing;
  }
  return COPY.steps.thinking;
}
