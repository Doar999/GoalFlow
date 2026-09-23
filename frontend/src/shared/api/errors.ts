import type { components } from "@/shared/api/generated/schema";

export type ApiErrorCode = components["schemas"]["ErrorCode"];
export type ApiErrorBody = components["schemas"]["ErrorResponse"];

/** 归一化后的失败结果。UI 只消费这个类型，不直接读裸响应体。 */
export interface ApiFailure {
  readonly code: ApiErrorCode | "UNKNOWN";
  readonly message: string;
  readonly retryable: boolean;
  readonly requestId: string | null;
}

export const UNKNOWN_FAILURE_MESSAGE = "请求失败，请稍后重试";

/**
 * 每个错误码的兜底文案，用于服务端没给 message 的情况。
 *
 * 类型是 Record<ApiErrorCode, string>，所以后端新增错误码、前端重新生成类型之后，
 * 这里漏一个就编译不过——这正是我们想要的耦合：契约变了，前端必须显式处理。
 */
const FALLBACK_MESSAGE_BY_CODE: Record<ApiErrorCode, string> = {
  VALIDATION_FAILED: "提交的内容不符合要求，请检查后重试",
  UNAUTHENTICATED: "登录状态已失效，请重新登录",
  FORBIDDEN: "没有访问该内容的权限",
  NOT_FOUND: "内容不存在或已被删除",
  INTERNAL_ERROR: "服务内部错误，请稍后重试",
  RATE_LIMITED: "尝试次数过多，请稍后再试",
  INVALID_CREDENTIALS: "账号或密码错误",
  ACCOUNT_IDENTIFIER_UNAVAILABLE: "该账号标识不可用，请换一个",
  REGISTRATION_CLOSED: "本实例已关闭新用户注册",
  IDEMPOTENCY_KEY_CONFLICT: "这次提交与之前的请求内容不一致，请刷新后重试",
  REVISION_CONFLICT: "内容已被更新，请刷新后重试",
  BUDGET_CONFLICT: "时间预算不足，请调整安排后重试",
  DEPENDENCY_CYCLE: "这样会让任务依赖形成环，无法保存",
  CONFIRMATION_REQUIRED: "这是一次重大调整，需要你确认后才会生效",
  INPUT_STALE: "依据的内容已过期，请重新获取后重试",
  MODEL_UNAVAILABLE: "模型服务暂时不可用，请稍后重试",
  GOAL_STATE_CONFLICT: "目标当前状态下不能进行这个操作",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function isApiErrorCode(value: string): value is ApiErrorCode {
  return Object.hasOwn(FALLBACK_MESSAGE_BY_CODE, value);
}

function readString(source: Record<string, unknown>, key: string): string | null {
  const value = source[key];
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

/**
 * 把任意响应体归一化成 ApiFailure。
 *
 * 对形状不做假设：网关、代理或旧版本服务端都可能返回不符合契约的内容，
 * 那时不能让界面崩在解析上。
 */
export function toApiFailure(body: unknown): ApiFailure {
  if (!isRecord(body)) {
    return { code: "UNKNOWN", message: UNKNOWN_FAILURE_MESSAGE, retryable: false, requestId: null };
  }

  const rawCode = readString(body, "code");
  const code = rawCode !== null && isApiErrorCode(rawCode) ? rawCode : "UNKNOWN";
  const message =
    readString(body, "message") ??
    (code === "UNKNOWN" ? UNKNOWN_FAILURE_MESSAGE : FALLBACK_MESSAGE_BY_CODE[code]);

  return {
    code,
    message,
    retryable: body["retryable"] === true,
    requestId: readString(body, "request_id"),
  };
}
