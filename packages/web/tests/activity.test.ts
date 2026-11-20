import { describe, expect, it } from "vitest";
import { activityLabel, toolLabel } from "../app/utils/activity";
import { COPY } from "../app/utils/copy";
import { MessageBuilder, toolResultMessage } from "../app/utils/message";
import type { Message } from "../app/types/dto";
import products from "./fixtures/products.json";

const user = MessageBuilder.createUserMessage("q");
const blank = MessageBuilder.createAssistantMessage();
const call = (name: string) => MessageBuilder.createToolUseMessage(name, "t1");
const productResult = toolResultMessage({ status: "success", content: [{ text: JSON.stringify({ schemaVersion: "product.v1", products }) }] }, "search_products");

describe("step labels", () => {
  it("keep the labels of known tools, with or without a Gateway target prefix", () => {
    expect(toolLabel("search_products")).toBe(COPY.steps.searching);
    expect(toolLabel("catalog___search_products")).toBe(COPY.steps.searching);
    expect(toolLabel("saved-items___get_saved_items")).toBe(COPY.steps.savedItems);
  });

  it("never show an internal tool name", () => {
    for (const name of ["crm___lookup_customer", "lookup_customer", "", undefined, "constructor", "__proto__"]) {
      const label = toolLabel(name);
      expect(label).toBe(COPY.steps.otherTool);
      expect(label).not.toContain("___");
    }
    expect(activityLabel([user, blank, call("crm___lookup_customer")])).toBe("정보를 확인하고 있어요");
  });

  it("say products are being arranged only after a result with products", () => {
    expect(activityLabel([user, blank, call("search_products"), productResult, MessageBuilder.createAssistantMessage()])).toBe(COPY.steps.arranging);
    for (const result of [
      toolResultMessage({ status: "success" }, "crm___lookup_customer"),
      toolResultMessage({ status: "success", content: [{ text: '{"rows":[1,2]}' }] }, "crm___lookup_customer"),
      toolResultMessage({ status: "success", content: [{ text: JSON.stringify({ schemaVersion: "product.v1", products: [] }) }] }, "search_products"),
    ]) {
      const label = activityLabel([user, blank, call("crm___lookup_customer"), result, MessageBuilder.createAssistantMessage()]);
      expect(label).toBe(COPY.steps.composing);
      expect(label).not.toBe(COPY.steps.arranging);
    }
  });

  it("mention search conditions only after a failed search", () => {
    const failedSearch = toolResultMessage({ status: "error" }, "catalog___search_products");
    const failedOther = toolResultMessage({ status: "error" }, "crm___lookup_customer");
    expect(activityLabel([user, blank, failedSearch, MessageBuilder.createAssistantMessage()])).toBe(COPY.steps.retrying);
    expect(activityLabel([user, blank, failedOther, MessageBuilder.createAssistantMessage()])).toBe(COPY.steps.composing);
  });

  it("fall back to thinking otherwise", () => {
    const messages: Message[][] = [[user], [user, blank], [user, MessageBuilder.createAssistantMessage("쓰는 중")]];
    for (const list of messages) expect(activityLabel(list)).toBe(COPY.steps.thinking);
  });
});
