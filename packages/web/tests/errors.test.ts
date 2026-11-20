import { describe, expect, it } from "vitest";
import { COPY } from "../app/utils/copy";
import {
  ApiError,
  AuthConfigError,
  AuthError,
  NetworkError,
  ProtocolError,
  StreamError,
  callbackErrorMessage,
  describeError,
} from "../app/utils/errors";

const frame = (code: string, retryable?: boolean) => new StreamError(code, retryable, "English developer detail");

describe("error copy", () => {
  it("maps every stream error code to Korean copy and an action", () => {
    expect(describeError(frame("turn_limit"))).toEqual({ message: "대화가 길어졌어요. 새 대화를 시작해 주세요.", action: "newConversation" });
    expect(describeError(frame("busy"))).toEqual({ message: "이전 답변을 아직 작성하고 있어요. 잠시 후 다시 보내 주세요.", action: "retry" });
    expect(describeError(frame("owner_mismatch"))).toEqual({ message: COPY.errors.ownerMismatch, action: "newConversation" });
    expect(describeError(frame("invalid_request"))).toEqual({ message: COPY.errors.invalidRequest });
    for (const [code, message] of [
      ["throttled", COPY.errors.throttled],
      ["upstream", COPY.errors.upstream],
      ["internal", COPY.errors.internal],
      ["init_failed", COPY.errors.initFailed],
      ["deadline", COPY.errors.deadline],
    ] as const) {
      expect(describeError(frame(code, true))).toEqual({ message, action: "retry" });
    }
  });

  it("handles error frames with a missing or unknown code", () => {
    expect(describeError(new StreamError("", undefined, "Agent invocation failed"))).toEqual({ message: COPY.errors.generic, action: "retry" });
    expect(describeError(new StreamError("new_code", false, "x"))).toEqual({ message: COPY.errors.generic });
  });

  it("maps HTTP statuses and network failures", () => {
    expect(describeError(new ApiError(401))).toEqual({ message: COPY.errors.authExpired, action: "signIn" });
    expect(describeError(new ApiError(403))).toEqual({ message: COPY.errors.forbidden, action: "signIn" });
    expect(describeError(new ApiError(429))).toEqual({ message: COPY.errors.throttled, action: "retry" });
    expect(describeError(new ApiError(422))).toEqual({ message: COPY.errors.invalidRequest });
    for (const status of [500, 502, 503, 504]) expect(describeError(new ApiError(status))).toEqual({ message: COPY.errors.internal, action: "retry" });
    expect(describeError(new ApiError(503, "busy"))).toEqual({ message: COPY.errors.busy, action: "retry" });
    expect(describeError(new ApiError(403, "owner_mismatch"))).toEqual({ message: COPY.errors.ownerMismatch, action: "newConversation" });
    expect(describeError(new TypeError("Failed to fetch"))).toEqual({ message: COPY.errors.network, action: "retry" });
    expect(describeError(new NetworkError())).toEqual({ message: COPY.errors.network, action: "retry" });
    expect(describeError(new ProtocolError())).toEqual({ message: COPY.errors.unreadable });
  });

  it("asks for a new sign-in whatever the context", () => {
    const fallback = { message: COPY.history.loadFailed, action: "retry" as const };
    expect(describeError(new AuthError("expired"), fallback)).toEqual({ message: COPY.errors.authExpired, action: "signIn" });
    expect(describeError(new AuthError("signedOut"), fallback)).toEqual({ message: COPY.errors.signedOut, action: "signIn" });
    expect(describeError(new ApiError(500), fallback)).toBe(fallback);
  });

  it("never shows an exception's text or an HTTP body", () => {
    const leaks = [new Error("HTTP 502: {\"detail\":\"boom\"}"), new StreamError("internal", true, "Traceback: boom"), new ApiError(500), "boom", undefined];
    for (const e of leaks) {
      const { message } = describeError(e);
      expect(message).not.toMatch(/HTTP|boom|Traceback|[A-Za-z]{4,}/);
    }
  });
});

describe("sign-in callback copy", () => {
  const response = (error: string) => Object.assign(new Error(error), { name: "ErrorResponse", error });

  it("explains why the callback failed", () => {
    expect(callbackErrorMessage(response("access_denied"))).toBe(COPY.auth.callbackCancelled);
    expect(callbackErrorMessage(response("invalid_grant"))).toBe(COPY.auth.callbackFailed);
    expect(callbackErrorMessage(new Error("No matching state found in storage"))).toBe(COPY.auth.callbackStale);
    expect(callbackErrorMessage(new Error("No state in response"))).toBe(COPY.auth.callbackStale);
    expect(callbackErrorMessage(new TypeError("Failed to fetch"))).toBe(COPY.auth.callbackNetwork);
    expect(callbackErrorMessage(new AuthConfigError())).toBe(COPY.auth.unavailable);
    expect(callbackErrorMessage("??")).toBe(COPY.auth.callbackFailed);
  });
});

describe("copy", () => {
  // Every string in COPY, with sample arguments for the templates.
  function strings(value: unknown, path = "COPY"): [string, string][] {
    if (typeof value === "string") return [[path, value]];
    if (typeof value === "function") return [[path, String(value("Cotton Shirt"))]];
    return Object.entries(value as object).flatMap(([key, child]) => strings(child, `${path}.${key}`));
  }
  const all = strings(COPY);

  it("speaks 해요체 and says 대화, never 세션", () => {
    expect(all.length).toBeGreaterThan(40);
    for (const [path, text] of all) {
      expect(text, path).not.toMatch(/니다|세션|해주세요/);
    }
    // Sentences end in -요; labels and prompts are not sentences.
    const sentences = all.filter(([path]) => !/\.(actions|prompts|untitled|deleteLabel|current)/.test(path));
    for (const [path, text] of sentences) expect(text, path).toMatch(/요[.…]?$/);
  });

  it("asks for similar products without a particle that depends on the name", () => {
    for (const name of ["Cotton Shirt", "겨울 자켓", "울 코트"]) {
      const prompt = COPY.prompts.similar(name);
      expect(prompt).toContain(name);
      expect(prompt).not.toMatch(new RegExp(`${name}(와|과)`));
    }
  });
});
