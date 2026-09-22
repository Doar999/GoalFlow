import { describe, expect, it } from "vitest";

import { UNKNOWN_FAILURE_MESSAGE, isApiErrorCode, toApiFailure } from "@/shared/api/errors";

describe("toApiFailure", () => {
  it("保留服务端给出的码、文案、可重试与 request_id", () => {
    const failure = toApiFailure({
      code: "REVISION_CONFLICT",
      message: "计划版本已过期，请刷新后重试",
      request_id: "req_0000000000",
      retryable: false,
      details: {},
    });

    expect(failure).toEqual({
      code: "REVISION_CONFLICT",
      message: "计划版本已过期，请刷新后重试",
      retryable: false,
      requestId: "req_0000000000",
    });
  });

  it("服务端没给 message 时用该错误码的兜底文案", () => {
    const failure = toApiFailure({ code: "MODEL_UNAVAILABLE", retryable: true });

    expect(failure.code).toBe("MODEL_UNAVAILABLE");
    expect(failure.message).not.toBe("");
    expect(failure.message).not.toBe(UNKNOWN_FAILURE_MESSAGE);
    expect(failure.retryable).toBe(true);
  });

  it("未知错误码退回 UNKNOWN，但仍展示服务端文案", () => {
    const failure = toApiFailure({ code: "SOMETHING_NEW", message: "服务端说了点新东西" });

    expect(failure.code).toBe("UNKNOWN");
    expect(failure.message).toBe("服务端说了点新东西");
  });

  it.each([undefined, null, "纯文本", 42, []])("不符合契约的响应体不会让解析崩掉：%s", (body) => {
    const failure = toApiFailure(body);

    expect(failure.code).toBe("UNKNOWN");
    expect(failure.message).toBe(UNKNOWN_FAILURE_MESSAGE);
    expect(failure.requestId).toBeNull();
  });

  it("空白 message 视为没给", () => {
    expect(toApiFailure({ code: "NOT_FOUND", message: "   " }).message).not.toBe("   ");
  });

  it("retryable 只认布尔真值，不认字符串", () => {
    expect(toApiFailure({ code: "INTERNAL_ERROR", retryable: "true" }).retryable).toBe(false);
  });
});

describe("isApiErrorCode", () => {
  it("识别契约里的错误码", () => {
    expect(isApiErrorCode("BUDGET_CONFLICT")).toBe(true);
    expect(isApiErrorCode("DEPENDENCY_CYCLE")).toBe(true);
  });

  it("拒绝契约之外的字符串", () => {
    expect(isApiErrorCode("budget_conflict")).toBe(false);
    expect(isApiErrorCode("toString")).toBe(false);
  });
});
