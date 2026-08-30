import { api } from "@/lib/api";
import type { Settings, TestResult } from "@/lib/types";

export const settingsApi = {
  get: () => api.get<Settings>("/settings"),
  put: (body: Settings) => api.put<Settings>("/settings", body),
  testProwlarr: (body: Pick<Settings, "prowlarrUrl" | "prowlarrApiKey">) =>
    api.post<TestResult>("/settings/test-prowlarr", body),
  testApprise: (body: Pick<Settings, "appriseUrls">) =>
    api.post<TestResult>("/settings/test-apprise", body),
};
