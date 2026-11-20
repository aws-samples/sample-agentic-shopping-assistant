import { readFileSync } from "node:fs";
import { describe, it, expect } from "vitest";
import { splitAnswer, withoutUnfinishedCitation } from "../app/utils/citations";
import { renderAnswer } from "../app/utils/markdown";
import { groupTurns, productsById } from "../app/utils/turns";
import { toolResultMessage, MessageBuilder } from "../app/utils/message";
import type { Product } from "../app/types/domain";
import type { Message } from "../app/types/dto";
import products from "./fixtures/products.json";

const catalog = new Map((products as Product[]).map((product) => [product.id, product]));
// The renderer Answer.vue uses, so the tests cover its real configuration.
const render = (text: string, restored = false) => renderAnswer(text, { products: catalog, restored });

describe("card slots", () => {
  it("turns a citation on its own line into a card between heading and explanation", () => {
    const answer = "따뜻한 자켓을 골랐어요.\n\n### 출근길 보온\n\n[[Product:sample-1]]\n\n가볍게 걸치기 좋아요.";
    expect(splitAnswer(answer, false)).toEqual([
      { kind: "text", text: "따뜻한 자켓을 골랐어요.\n\n### 출근길 보온\n" },
      { kind: "card", id: "sample-1" },
      { kind: "text", text: "\n가볍게 걸치기 좋아요." },
    ]);
  });

  it("leaves a citation inside a sentence in the prose", () => {
    expect(splitAnswer("[[Product:sample-2]]는 여름용이에요.", false)).toEqual([
      { kind: "text", text: "[[Product:sample-2]]는 여름용이에요." },
    ]);
  });

  it("accepts every id the sample catalog returns", () => {
    for (const product of products as Product[]) {
      expect(splitAnswer(`[[Product:${product.id}]]`, false)).toEqual([{ kind: "card", id: product.id }]);
    }
  });

  it("parses the citation example the agent prompt teaches", () => {
    const prompt = readFileSync(new URL("../../agent/src/prompts/system.md", import.meta.url), "utf8");
    const cards = splitAnswer(prompt, false).filter((segment) => segment.kind === "card");
    expect(cards).toEqual([{ kind: "card", id: "sample-1" }]);
    expect(catalog.has("sample-1")).toBe(true);
  });
});

describe("streaming", () => {
  it("never shows a citation the model is still writing", () => {
    for (const partial of ["[", "[[", "[[Prod", "[[Product:", "[[Product:sample-", "[[Product:sample-1]"]) {
      expect(withoutUnfinishedCitation(`골랐어요.\n\n${partial}`, true)).toBe("골랐어요.\n\n");
    }
  });

  it("keeps a finished citation and ordinary trailing brackets once complete", () => {
    expect(withoutUnfinishedCitation("[[Product:sample-1]]", true)).toBe("[[Product:sample-1]]");
    expect(withoutUnfinishedCitation("목록 [", false)).toBe("목록 [");
  });
});

describe("inline citations", () => {
  it("show the returned product's name", () => {
    expect(render("[[Product:sample-2]]는 여름용이에요.")).toContain('<span class="product-cite">Cotton Shirt</span>는 여름용이에요.');
  });

  it("mark an id the search did not return", () => {
    expect(render("[[Product:made-up]] 추천해요.")).toContain("확인할 수 없는 상품");
  });

  it("fall back to plain text in restored history, which has no product data", () => {
    const html = render("[[Product:made-up]] 추천해요.", true);
    expect(html).toContain("이 상품 추천해요.");
    expect(html).not.toContain("product-cite");
  });

  it("escape catalog strings and model markup", () => {
    const hostile = new Map([["x-1", { ...(products as Product[])[0]!, id: "x-1", productDisplayName: '<img src=x onerror="alert(1)">' }]]);
    const html = renderAnswer("[[Product:x-1]] <script>alert(1)</script>", { products: hostile, restored: false });
    expect(html).not.toContain("<img");
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;");
  });
});

describe("turns", () => {
  const searchResult = toolResultMessage(
    { toolUseId: "t1", status: "success", content: [{ text: JSON.stringify({ schemaVersion: "product.v1", products }) }] },
    "search_products",
  );
  const turn: Message[] = [
    MessageBuilder.createUserMessage("남성 겨울 자켓 보여줘"),
    MessageBuilder.createAssistantMessage("남성 겨울 자켓을 찾아볼게요."),
    MessageBuilder.createToolUseMessage("search_products", "t1"),
    searchResult,
    MessageBuilder.createAssistantMessage("\n\n[[Product:sample-1]]"),
  ];

  it("show one answer per turn without listing search results", () => {
    const turns = groupTurns(turn);
    expect(turns.map((t) => t.role)).toEqual(["user", "assistant"]);
    const answer = turns[1]!;
    expect(answer.role === "assistant" && answer.parts.map((m) => m.content)).toEqual([
      "남성 겨울 자켓을 찾아볼게요.",
      "\n\n[[Product:sample-1]]",
    ]);
  });

  it("hide failed tool calls and tool output that has no renderer", () => {
    const failed = toolResultMessage({ status: "error", content: [{ text: "Invalid season" }] });
    const unknown = toolResultMessage({ status: "success", content: [{ text: '{"customer":{"email":"a@example.com"}}' }] });
    const turns = groupTurns([
      MessageBuilder.createUserMessage("q"),
      MessageBuilder.createAssistantMessage(""),
      MessageBuilder.createToolUseMessage("crm___lookup_customer", "t1"),
      failed,
      MessageBuilder.createAssistantMessage("다시 찾아볼게요."),
      MessageBuilder.createToolUseMessage("crm___lookup_customer", "t2"),
      unknown,
      MessageBuilder.createAssistantMessage("확인했어요."),
    ]);
    const answer = turns[1]!;
    expect(failed.failed).toBe(true);
    expect(answer.role === "assistant" && answer.parts.map((m) => m.content)).toEqual(["다시 찾아볼게요.", "확인했어요."]);
    expect(JSON.stringify(turns)).not.toContain("a@example.com");
    expect(JSON.stringify(turns)).not.toContain("crm___lookup_customer");
  });

  it("keep notes on how an answer ended in its turn", () => {
    const turns = groupTurns([
      MessageBuilder.createUserMessage("q"),
      MessageBuilder.createAssistantMessage(""),
      MessageBuilder.createNotice("stopped"),
    ]);
    expect(turns[1]).toEqual({ role: "assistant", parts: [MessageBuilder.createNotice("stopped")] });
  });

  it("resolve citations only against validated search results", () => {
    expect([...productsById(turn).keys()]).toEqual((products as Product[]).map((p) => p.id));
    expect(productsById([MessageBuilder.createAssistantMessage("[[Product:sample-1]]")]).size).toBe(0);
  });
});
