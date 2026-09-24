import { apiClient } from "@/shared/api/client";
import { requireData } from "@/shared/api/result";
import type { components } from "@/shared/api/generated/schema";

export type Agenda = components["schemas"]["AgendaResponse"];

export async function getAgenda(localDate: string): Promise<Agenda> {
  return requireData(
    await apiClient.GET("/api/agendas/{local_date}", {
      params: { path: { local_date: localDate } },
    }),
  );
}
