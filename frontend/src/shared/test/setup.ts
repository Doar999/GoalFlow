import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterAll, afterEach } from "vitest";

import { server } from "@/shared/test/server";

// 必须在模块顶层 listen，不能放进 beforeAll。
// openapi-fetch 在 createClient 时就抓住了 globalThis.fetch 的引用，
// 而 setup 文件先于测试模块导入——beforeAll 太晚，补丁打不到那个引用上。
//
// onUnhandledRequest: "error" 让"忘记声明桩"直接失败，而不是静默走真实网络。
server.listen({ onUnhandledRequest: "error" });

afterEach(() => {
  server.resetHandlers();
  cleanup();
});

afterAll(() => server.close());
