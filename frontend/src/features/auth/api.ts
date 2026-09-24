import { apiClient } from "@/shared/api/client";
import { toApiFailure } from "@/shared/api/errors";
import { requireData } from "@/shared/api/result";
import type { components } from "@/shared/api/generated/schema";

export type AuthSession = components["schemas"]["AuthSessionResponse"];
export type RegisterInput = components["schemas"]["RegisterRequest"];
export type LoginInput = components["schemas"]["LoginRequest"];
export const SESSION_QUERY_KEY = ["auth", "session"] as const;

export async function getSession(): Promise<AuthSession> {
  return requireData(await apiClient.GET("/api/auth/session"), false);
}

export async function getRegistrationStatus(): Promise<boolean> {
  const result = requireData(await apiClient.GET("/api/auth/registration"));
  return result.registration_open;
}

export async function login(input: LoginInput): Promise<AuthSession> {
  return requireData(await apiClient.POST("/api/auth/login", { body: input }), false);
}

export async function register(input: RegisterInput): Promise<AuthSession> {
  return requireData(await apiClient.POST("/api/auth/register", { body: input }), false);
}

export async function logout(): Promise<void> {
  const { error } = await apiClient.POST("/api/auth/logout");
  if (error !== undefined) {
    throw toApiFailure(error);
  }
}
