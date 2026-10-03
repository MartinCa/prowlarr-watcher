import type { ActionStatus } from "@/hooks/use-async-action";

export interface GrabState {
  /** The POST that enqueues the grab job is in flight. */
  requestPending: boolean;
  /** That POST was rejected (bad GUID, CSRF, server error). */
  requestFailed: boolean;
  jobId: string | undefined;
  /** Status reported by the polled job, once the first poll has answered. */
  jobStatus: "queued" | "running" | "retrying" | "done" | "error" | undefined;
  /** Polling the job itself failed (network, expired job). Ignored once the job reported `done`. */
  pollFailed: boolean;
}

/** Collapses the grab mutation and the polled job into the status shown on the Grab icon. */
export function deriveGrabStatus(state: GrabState): ActionStatus {
  const { requestPending, requestFailed, jobId, jobStatus, pollFailed } = state;
  const jobInFlight =
    !!jobId &&
    !pollFailed &&
    (!jobStatus || jobStatus === "queued" || jobStatus === "running" || jobStatus === "retrying");

  if (requestPending || jobInFlight) return "pending";
  // A finished grab stays a success even if a later background refetch of the job fails: React
  // Query keeps the last good data, so `done` is still known and must not flip back to error.
  if (jobStatus === "done") return "success";
  if (requestFailed || pollFailed || jobStatus === "error") return "error";
  return "idle";
}
