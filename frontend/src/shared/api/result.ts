import { toApiFailure } from "@/shared/api/errors";

type ApiResult<T> = {
  data?: T;
  error?: unknown;
  response: Response;
};

export function requireData<T>(result: ApiResult<T>, signalSessionExpiry = true): T {
  if (result.response.status === 401 && signalSessionExpiry && typeof window !== "undefined") {
    window.dispatchEvent(new Event("goalflow:session-expired"));
  }
  if (result.error !== undefined) {
    throw toApiFailure(result.error);
  }
  if (result.data === undefined) {
    throw toApiFailure(undefined);
  }
  return result.data;
}
