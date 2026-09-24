import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it } from "vitest";

import { App } from "@/app/App";
import { AppProviders, createQueryClient } from "@/app/providers";
import { server } from "@/shared/test/server";

const session = {
  user: { id: "user-a", account_identifier: "user-a", role: "user", timezone: "Asia/Shanghai" },
  session: { created_at: "2026-09-24T00:00:00Z", expires_at: "2026-10-24T00:00:00Z" },
};

const unauthenticated = {
  code: "UNAUTHENTICATED",
  message: "请先登录",
  request_id: "req-1",
  retryable: false,
  details: {},
};

function renderAt(path: string) {
  window.history.replaceState({}, "", path);
  const client = createQueryClient();
  render(
    <AppProviders client={client}>
      <App />
    </AppProviders>,
  );
  return client;
}

afterEach(() => window.history.replaceState({}, "", "/"));

describe("前端核心入口", () => {
  it("未登录时不读取私有页面数据，登录后返回原路径", async () => {
    let agendaReads = 0;
    let authenticated = false;
    server.use(
      http.get("*/api/auth/session", () =>
        authenticated
          ? HttpResponse.json(session)
          : HttpResponse.json(unauthenticated, { status: 401 }),
      ),
      http.get("*/api/auth/registration", () => HttpResponse.json({ registration_open: true })),
      http.post("*/api/auth/login", () => {
        authenticated = true;
        return HttpResponse.json(session);
      }),
      http.get("*/api/agendas/*", () => {
        agendaReads += 1;
        return HttpResponse.json({});
      }),
    );
    renderAt("/goals/new");

    expect(await screen.findByRole("heading", { name: "登录 GoalFlow" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/login");
    expect(window.location.search).toContain("next=%2Fgoals%2Fnew");
    expect(agendaReads).toBe(0);
    expect(screen.queryByRole("textbox", { name: "目标描述" })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("账号标识"), { target: { value: "user-a" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "example-password" } });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));
    expect(await screen.findByRole("textbox", { name: "目标描述" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/goals/new");
    expect(agendaReads).toBe(0);
  });

  it("注册关闭时不展示注册表单，仍提供登录入口", async () => {
    server.use(
      http.get("*/api/auth/registration", () => HttpResponse.json({ registration_open: false })),
    );
    renderAt("/register");

    expect(await screen.findByText(/此实例当前不开放新账号注册/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "创建账号" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "前往登录" })).toBeInTheDocument();
  });

  it("密码错误时保留登录表单并展示服务端说明", async () => {
    server.use(
      http.get("*/api/auth/registration", () => HttpResponse.json({ registration_open: true })),
      http.post("*/api/auth/login", () =>
        HttpResponse.json(
          {
            code: "INVALID_CREDENTIALS",
            message: "账号或密码错误",
            request_id: "req-2",
            retryable: false,
            details: {},
          },
          { status: 401 },
        ),
      ),
    );
    renderAt("/login");
    fireEvent.change(await screen.findByLabelText("账号标识"), { target: { value: "user-a" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "wrong-password" } });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("账号或密码错误");
    expect(screen.getByLabelText("账号标识")).toBeInTheDocument();
    expect(window.location.pathname).toBe("/login");
  });

  it("登录后能看到真实的今日空态并创建目标草稿", async () => {
    let createdBody: unknown;
    let idempotencyKey: string | null = null;
    server.use(
      http.get("*/api/auth/registration", () => HttpResponse.json({ registration_open: true })),
      http.post("*/api/auth/login", () => HttpResponse.json(session)),
      http.get("*/api/auth/session", () => HttpResponse.json(session)),
      http.get("*/api/agendas/*", () =>
        HttpResponse.json({ local_date: "2026-09-24", planning_revision: 0, current: null }),
      ),
      http.post("*/api/goals", async ({ request }) => {
        createdBody = await request.json();
        idempotencyKey = request.headers.get("Idempotency-Key");
        return HttpResponse.json(
          {
            id: "goal-1",
            title: "三个月后完成英语工作介绍",
            status: "draft",
            kind: "achievement",
            domain: "general",
            domain_confidence: null,
            review_period: "weekly",
            active_profile_id: null,
            current_plan_version_id: null,
            source_goal_id: null,
            paused_at: null,
            pause_reason: null,
            closed_at: null,
            closure_kind: null,
            closure_note: null,
            created_at: "2026-09-24T00:00:00Z",
            updated_at: "2026-09-24T00:00:00Z",
            revision: 0,
          },
          { status: 201 },
        );
      }),
      http.get("*/api/goals/goal-1", () =>
        HttpResponse.json({
          id: "goal-1",
          title: "三个月后完成英语工作介绍",
          status: "draft",
          kind: "achievement",
          domain: "general",
          domain_confidence: null,
          review_period: "weekly",
          active_profile_id: null,
          current_plan_version_id: null,
          source_goal_id: null,
          paused_at: null,
          pause_reason: null,
          closed_at: null,
          closure_kind: null,
          closure_note: null,
          created_at: "2026-09-24T00:00:00Z",
          updated_at: "2026-09-24T00:00:00Z",
          revision: 0,
        }),
      ),
      http.get("*/api/goals/goal-1/profile-draft", () =>
        HttpResponse.json({
          goal_id: "goal-1",
          content: { initial_description: "三个月后完成英语工作介绍" },
          source_map: {},
          gaps: [],
          assumptions: [],
          contradictions: [],
          readiness: "needs_input",
          revision: 0,
          updated_at: "2026-09-24T00:00:00Z",
        }),
      ),
    );
    renderAt("/login");
    fireEvent.change(await screen.findByLabelText("账号标识"), { target: { value: "user-a" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "example-password" } });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));

    expect(await screen.findByText("今天还没有排期结果")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "写下第一个目标" }));
    fireEvent.change(await screen.findByLabelText("目标描述"), {
      target: { value: "三个月后完成英语工作介绍" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存目标草稿" }));

    expect(
      await screen.findByRole("heading", { name: "三个月后完成英语工作介绍" }),
    ).toBeInTheDocument();
    expect(createdBody).toEqual({
      title: "三个月后完成英语工作介绍",
      initial_description: "三个月后完成英语工作介绍",
    });
    expect(idempotencyKey).toBeTruthy();
    expect(screen.getByText(/目标澄清和方案生成尚未接入模型作业/)).toBeInTheDocument();
    await waitFor(() => expect(window.location.pathname).toBe("/goals/goal-1"));
  });

  it("退出时清除私有缓存并返回登录页", async () => {
    server.use(
      http.get("*/api/auth/session", () => HttpResponse.json(session)),
      http.get("*/api/auth/registration", () => HttpResponse.json({ registration_open: true })),
      http.get("*/api/agendas/*", () =>
        HttpResponse.json({ local_date: "2026-09-24", planning_revision: 0, current: null }),
      ),
      http.post("*/api/auth/logout", () => new HttpResponse(null, { status: 204 })),
    );
    const client = renderAt("/today");
    expect(await screen.findByText("今天还没有排期结果")).toBeInTheDocument();
    client.setQueryData(["goals", "user-a", "goal-private"], { title: "私有目标" });
    const button = screen.getAllByRole("button", { name: "退出登录" })[0];
    if (button === undefined) throw new Error("缺少退出按钮");
    fireEvent.click(button);

    await waitFor(() => expect(window.location.pathname).toBe("/login"));
    expect(client.getQueryData(["goals", "user-a", "goal-private"])).toBeUndefined();
    expect(screen.queryByText("私有目标")).not.toBeInTheDocument();
  });

  it("私有接口返回 401 后撤下旧账号内容", async () => {
    let sessionReads = 0;
    server.use(
      http.get("*/api/auth/session", () => {
        sessionReads += 1;
        return sessionReads === 1
          ? HttpResponse.json(session)
          : HttpResponse.json(unauthenticated, { status: 401 });
      }),
      http.get("*/api/auth/registration", () => HttpResponse.json({ registration_open: true })),
      http.get("*/api/agendas/*", () => HttpResponse.json(unauthenticated, { status: 401 })),
    );
    const client = renderAt("/today");
    client.setQueryData(["goals", "user-a", "goal-private"], { title: "私有目标" });

    expect(await screen.findByRole("heading", { name: "登录 GoalFlow" })).toBeInTheDocument();
    expect(client.getQueryData(["goals", "user-a", "goal-private"])).toBeUndefined();
    expect(screen.queryByText("今天，从清晰的一步开始。")).not.toBeInTheDocument();
  });
});
