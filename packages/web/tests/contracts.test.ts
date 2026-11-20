import { describe, it, expect } from "vitest";
import { SSEDecoder } from "../app/utils/sse";
import { MessageBuilder, toolResultMessage } from "../app/utils/message";
import { applyStreamEvent, type TurnState } from "../app/utils/stream-events";
import { groupTurns } from "../app/utils/turns";
import { COPY } from "../app/utils/copy";
import { StreamError } from "../app/utils/errors";
import type { Message, StreamEvent } from "../app/types/dto";
import products from "./fixtures/products.json";

const productResult = (payload: unknown) => ({ toolUseId: "t1", status: "success", content: [{ text: JSON.stringify(payload) }] });

describe("stream transport", () => {
  it("decodes Korean characters and frames split at every byte", () => {
    const parser = new SSEDecoder();
    const raw = new TextEncoder().encode('data: {"version":1,"type":"textDelta",\r\ndata: "data":{"text":"겨울 자켓"}}\r\n\r\n');
    const result = [...raw].flatMap(byte => parser.feed(new Uint8Array([byte])));
    result.push(...parser.feed(new Uint8Array(), true));
    expect(result).toEqual([{ version: 1, type: "textDelta", data: { text: "겨울 자켓" } }]);
  });
  it("rejects unknown protocol versions", () => {
    expect(() => new SSEDecoder().feed(new TextEncoder().encode('data: {"version":2}\n\n'))).toThrow();
  });
});

describe("tool display contract", () => {
  it("renders the catalog fixture as a product.v1 result", () => {
    const result = toolResultMessage(productResult({ schemaVersion: "product.v1", products }));
    expect(result.type).toBe("toolResult");
    expect(result.products).toEqual(products);
  });

  it("keeps nothing of a result without a renderer, raw or not", () => {
    for (const data of [
      { toolUseId: "t1", status: "success" },
      { toolUseId: "t1", status: "success", content: [{ text: "Customer search result" }] },
      productResult({ schemaVersion: "crm.v1", customer: { email: "a@example.com" } }),
    ]) {
      expect(toolResultMessage(data)).toEqual({ role: "tool", type: "toolResult", content: "", toolUseId: "t1" });
    }
  });

  it("treats a tool error as failed, not as an empty search", () => {
    expect(toolResultMessage({ status: "error", content: [{ text: "Tool budget exhausted" }] })).toMatchObject({ failed: true, content: "" });
  });

  it("keeps only the valid products of a product.v1 result", () => {
    const result = toolResultMessage(productResult({ schemaVersion: "product.v1", products: [products[0], { id: "x" }] }));
    expect(result.failed).toBeUndefined();
    expect(result.products).toEqual([products[0]]);
  });

  it("treats a product.v1 result without a usable product as failed", () => {
    expect(toolResultMessage(productResult({ schemaVersion: "product.v1", products: [{ id: "x" }] })).failed).toBe(true);
    expect(toolResultMessage(productResult({ schemaVersion: "product.v1", products: "sample-1" })).failed).toBe(true);
  });

  it("keeps an empty search as a product result, not a failure", () => {
    const result = toolResultMessage(productResult({ schemaVersion: "product.v1", products: [] }));
    expect(result).toMatchObject({ type: "toolResult", products: [] });
    expect(result.failed).toBeUndefined();
  });

  it("survives malformed result frames", () => {
    for (const data of [undefined, null, "text", 42, [], { content: "not blocks" }, { content: [null, 1, { text: 2 }] }]) {
      expect(() => toolResultMessage(data)).not.toThrow();
      expect(toolResultMessage(data).products).toBeUndefined();
    }
  });
});

describe("stream event contract", () => {
  function run(...events: StreamEvent[]) {
    const messages: Message[] = [MessageBuilder.createUserMessage("q"), MessageBuilder.createAssistantMessage()];
    const turn: TurnState = { start: 0, answer: 1, done: false };
    for (const event of events) applyStreamEvent(messages, turn, event);
    const shown = groupTurns(messages).flatMap((t) => (t.role === "assistant" ? t.parts.map((part) => part.content) : []));
    return { messages, turn, shown };
  }
  const event = (type: string, data?: unknown): StreamEvent => ({ version: 1, type, data });

  it("streams text, and text after a tool result into a new message", () => {
    const { messages, shown } = run(
      event("textDelta", { text: "찾아볼게요." }),
      event("toolUse", { toolUseId: "t1", name: "search_products" }),
      event("toolResult", productResult({ schemaVersion: "product.v1", products })),
      event("textDelta", { text: "[[Product:sample-1]]" }),
      event("done", { stopReason: "end_turn" }),
    );
    expect(shown).toEqual(["찾아볼게요.", "[[Product:sample-1]]"]);
    // The result is matched to its call by toolUseId, for the step label.
    expect(messages.find((m) => m.type === "toolResult")?.toolName).toBe("search_products");
  });

  it("never puts a tool name or tool input into displayable text", () => {
    const { messages } = run(event("toolUse", { toolUseId: "t1", name: "crm___lookup_customer", input: { email: "a@example.com" } }));
    const call = messages.find((m) => m.type === "toolUse")!;
    expect(call.content).toBe("");
    expect(JSON.stringify(call)).not.toContain("a@example.com");
  });

  it("marks the turn done once, and notes an answer cut for length", () => {
    const { messages, turn } = run(
      event("textDelta", { text: "긴 답변" }),
      event("done", { stopReason: "max_tokens" }),
      event("done", { stopReason: "max_tokens" }),
    );
    expect(turn.done).toBe(true);
    expect(messages.filter((m) => m.type === "notice")).toEqual([MessageBuilder.createNotice("maxTokens")]);
  });

  it("replaces the whole answer with the guardrail's message", () => {
    const { shown } = run(
      event("textDelta", { text: "앞부분" }),
      event("toolUse", { toolUseId: "t1", name: "search_products" }),
      event("toolResult", { toolUseId: "t1", status: "success" }),
      event("textDelta", { text: "막힌 답변" }),
      event("done", { stopReason: "guardrail_intervened", message: "죄송합니다. 해당 요청에 대해서는 응답할 수 없습니다." }),
    );
    expect(shown).toEqual(["죄송합니다. 해당 요청에 대해서는 응답할 수 없습니다."]);
  });

  it("uses a Korean blocked message when the guardrail sends none", () => {
    const { shown } = run(event("textDelta", { text: "막힌 답변" }), event("done", { stopReason: "guardrail_intervened" }));
    expect(shown).toEqual([COPY.blockedAnswer]);
  });

  it("throws an error frame with its code; the developer message is only a detail", () => {
    try {
      run(event("error", { code: "turn_limit", retryable: false, message: "Session turn budget exhausted." }));
      expect.unreachable();
    } catch (e) {
      expect(e).toBeInstanceOf(StreamError);
      expect(e).toMatchObject({ code: "turn_limit", retryable: false, detail: "Session turn budget exhausted." });
    }
  });

  it("handles malformed and unknown frames without crashing", () => {
    const { shown, turn } = run(
      event("textDelta"),
      event("textDelta", { text: 7 }),
      event("toolUse", { name: "search_products", input: {} }),
      event("toolResult", null),
      event("future", { anything: true }),
      event("textDelta", { text: "끝" }),
    );
    expect(shown).toEqual(["끝"]);
    expect(turn.done).toBe(false);
    expect(() => run(event("error"))).toThrow(StreamError);
    expect(() => run(event("error", { message: "Agent invocation failed" }))).toThrow(StreamError);
  });
});
