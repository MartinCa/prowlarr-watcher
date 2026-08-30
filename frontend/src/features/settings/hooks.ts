import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { settingsApi } from "@/features/settings/api";
import type { Settings } from "@/lib/types";

const settingsKey = ["settings"] as const;

export function useSettings() {
  return useQuery({ queryKey: settingsKey, queryFn: settingsApi.get });
}

export function useSaveSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Settings) => settingsApi.put(body),
    onSuccess: (data) => queryClient.setQueryData(settingsKey, data),
  });
}

export function useTestProwlarr() {
  return useMutation({ mutationFn: settingsApi.testProwlarr });
}

export function useTestApprise() {
  return useMutation({ mutationFn: settingsApi.testApprise });
}
