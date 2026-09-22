import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { ApiFailure } from "@/shared/api/errors";
import { fetchHealth } from "@/shared/api/health";
import { server } from "@/shared/test/server";

describe("fetchHealth", () => {
  it("成功时返回契约定义的响应体", async () => {
    server.use(http.get("*/api/health", () => HttpResponse.json({ status: "ok" })));

    await expect(fetchHealth()).resolves.toEqual({ status: "ok" });
  });

  it("失败时抛出归一化后的 ApiFailure，而不是裸响应", async () => {
    server.use(
      http.get("*/api/health", () =>
        HttpResponse.json(
          {
            code: "INTERNAL_ERROR",
            message: "服务内部错误，请稍后重试",
            request_id: "req_abc",
            retryable: true,
            details: {},
          },
          { status: 500 },
        ),
      ),
    );

    await expect(fetchHealth()).rejects.toMatchObject<Partial<ApiFailure>>({
      code: "INTERNAL_ERROR",
      retryable: true,
      requestId: "req_abc",
    });
  });
});
