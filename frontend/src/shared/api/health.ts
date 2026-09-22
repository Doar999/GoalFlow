import { apiClient } from "@/shared/api/client";
import { toApiFailure } from "@/shared/api/errors";
import type { components } from "@/shared/api/generated/schema";

export type HealthResponse = components["schemas"]["HealthResponse"];

/**
 * 后端存活检查。
 *
 * 失败时抛 ApiFailure，调用方拿到的永远是归一化结果，不需要自己解析响应体。
 */
export async function fetchHealth(): Promise<HealthResponse> {
  const { data, error } = await apiClient.GET("/api/health");

  if (error !== undefined) {
    throw toApiFailure(error);
  }
  if (data === undefined) {
    throw toApiFailure(undefined);
  }
  return data;
}
