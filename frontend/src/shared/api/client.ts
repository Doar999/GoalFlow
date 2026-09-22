import createClient from "openapi-fetch";

import type { paths } from "@/shared/api/generated/schema";

/**
 * 同域部署：Nginx 同时提供网页与 /api，所以 baseUrl 就是页面自身的 origin。
 *
 * 不能直接写 "/"。fetch 在非浏览器环境（Node 的 undici，包括 jsdom 下的测试）
 * 要求绝对 URL，相对值会抛 ERR_INVALID_URL。
 */
function resolveBaseUrl(): string {
  return typeof window === "undefined" ? "http://localhost" : window.location.origin;
}

/**
 * 全局唯一的 HTTP 客户端。
 *
 * 组件里不允许出现裸 fetch，见 docs/engineering/03-code-and-test-standards.md 第 3 节。
 */
export const apiClient = createClient<paths>({
  baseUrl: resolveBaseUrl(),
  credentials: "same-origin",
});
