import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { queriesApi } from "@/features/queries/api";
import type { CreateQueryRequest, UpdateQueryRequest } from "@/lib/types";

const queryKeys = {
  all: ["queries"] as const,
  detail: (id: number) => ["queries", id] as const,
  queueStatus: ["queue-status"] as const,
  indexers: ["indexers"] as const,
  job: (jobId: string) => ["jobs", jobId] as const,
};

export function useQueries() {
  return useQuery({ queryKey: queryKeys.all, queryFn: queriesApi.list });
}

export function useQueryDetail(id: number) {
  return useQuery({ queryKey: queryKeys.detail(id), queryFn: () => queriesApi.get(id) });
}

/** Queued/running state for the query cards, polled while anything is in flight. */
export function useQueueStatus() {
  return useQuery({
    queryKey: queryKeys.queueStatus,
    queryFn: queriesApi.queueStatus,
    refetchInterval: (query) => {
      const data = query.state.data;
      const active = data ? Object.keys(data.queries).length > 0 || !!data.preview : false;
      return active ? 2000 : 10000;
    },
  });
}

export function useIndexers() {
  return useQuery({ queryKey: queryKeys.indexers, queryFn: queriesApi.indexers, retry: false });
}

/** Polls a search-preview job until it leaves the queued/running state. */
export function useJob(jobId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.job(jobId ?? ""),
    queryFn: () => queriesApi.getJob(jobId!),
    enabled: !!jobId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" || status === "retrying" ? 1000 : false;
    },
  });
}

export function useCreateQuery() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateQueryRequest) => queriesApi.create(body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.all }),
  });
}

export function useUpdateQuery(id: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: UpdateQueryRequest) => queriesApi.update(id, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.all });
      void queryClient.invalidateQueries({ queryKey: queryKeys.detail(id) });
    },
  });
}

export function useDeleteQuery() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => queriesApi.remove(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.all }),
  });
}

export function useRunQuery() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => queriesApi.run(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.queueStatus }),
  });
}

export function useSearchPreview() {
  return useMutation({
    mutationFn: (query: string) => queriesApi.searchPreview(query),
  });
}
