import { api } from "@/lib/api";
import type {
  CreateQueryRequest,
  Indexer,
  Job,
  Query,
  QueryDetail,
  QueueStatus,
  UpdateQueryRequest,
} from "@/lib/types";

export const queriesApi = {
  list: () => api.get<Query[]>("/queries"),
  get: (id: number) => api.get<QueryDetail>(`/queries/${id}`),
  create: (body: CreateQueryRequest) => api.post<Query>("/queries", body),
  update: (id: number, body: UpdateQueryRequest) => api.patch<Query>(`/queries/${id}`, body),
  remove: (id: number) => api.delete<void>(`/queries/${id}`),
  run: (id: number) => api.post<void>(`/queries/${id}/run`),
  queueStatus: () => api.get<QueueStatus>("/queue-status"),
  indexers: () => api.get<{ indexers: Indexer[] }>("/indexers"),
  searchPreview: (query: string) => api.post<{ jobId: string }>("/search-preview", { query }),
  getJob: (jobId: string) => api.get<Job>(`/jobs/${jobId}`),
};
