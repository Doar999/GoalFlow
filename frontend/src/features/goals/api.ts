import { apiClient } from "@/shared/api/client";
import { requireData } from "@/shared/api/result";
import type { components } from "@/shared/api/generated/schema";

export type Goal = components["schemas"]["GoalResponse"];
export type ProfileDraft = components["schemas"]["ProfileDraftResponse"];

export async function createGoal(description: string, idempotencyKey: string): Promise<Goal> {
  const title = description.trim().split(/\r?\n/, 1)[0]?.slice(0, 200) ?? "";
  return requireData(
    await apiClient.POST("/api/goals", {
      headers: { "Idempotency-Key": idempotencyKey },
      body: { title, initial_description: description.trim() },
    }),
  );
}

export async function getGoal(goalId: string): Promise<Goal> {
  return requireData(
    await apiClient.GET("/api/goals/{goal_id}", { params: { path: { goal_id: goalId } } }),
  );
}

export async function getProfileDraft(goalId: string): Promise<ProfileDraft> {
  return requireData(
    await apiClient.GET("/api/goals/{goal_id}/profile-draft", {
      params: { path: { goal_id: goalId } },
    }),
  );
}
