import { render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { App } from "@/app/App";
import { AppProviders, createQueryClient } from "@/app/providers";
import { server } from "@/shared/test/server";

function renderApp() {
  render(
    <AppProviders client={createQueryClient()}>
      <App />
    </AppProviders>,
  );
}

describe("工程自检页", () => {
  it("后端可用时展示状态", async () => {
    server.use(http.get("*/api/health", () => HttpResponse.json({ status: "ok" })));

    renderApp();

    expect(await screen.findByText(/后端状态：ok/)).toBeInTheDocument();
  });

  it("后端返回统一错误结构时展示可读文案，而不是原始码", async () => {
    server.use(
      http.get("*/api/health", () =>
        HttpResponse.json(
          {
            code: "MODEL_UNAVAILABLE",
            message: "模型服务暂时不可用，请稍后重试",
            request_id: "req_abc",
            retryable: true,
            details: {},
          },
          { status: 503 },
        ),
      ),
    );

    renderApp();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("模型服务暂时不可用");
    expect(alert).not.toHaveTextContent("MODEL_UNAVAILABLE");
  });
});
