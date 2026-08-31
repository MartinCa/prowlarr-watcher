import { Link } from "@tanstack/react-router";
import { CirclePlay, Pause, Play, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { QueryStatusBadge } from "@/features/queries/components/QueryStatusBadge";
import { useDeleteQuery, useRunQuery, useUpdateQuery } from "@/features/queries/hooks";
import { ApiError } from "@/lib/api";
import { formatRelativeTime } from "@/lib/format";
import type { Query } from "@/lib/types";
import { cn } from "@/lib/utils";

export function QueryCard({
  query,
  queueState,
}: {
  query: Query;
  queueState: "queued" | "running" | undefined;
}) {
  const updateQuery = useUpdateQuery(query.id);
  const deleteQuery = useDeleteQuery();
  const runQuery = useRunQuery();

  function handleDelete() {
    if (!confirm("Delete this query and all its results?")) return;
    deleteQuery.mutate(query.id, {
      onError: (error) => toast.error(error instanceof ApiError ? error.message : "Delete failed"),
    });
  }

  return (
    <div
      className={cn(
        "flex items-center justify-between gap-4 rounded-md border p-4",
        !query.enabled && "opacity-50",
      )}
    >
      <div>
        <Link
          to="/queries/$id"
          params={{ id: String(query.id) }}
          className="font-medium hover:underline"
        >
          {query.name}
        </Link>
        <div className="text-muted-foreground mt-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-1 font-mono text-xs">
          <span className="inline-flex items-center gap-1.5">
            <span
              className={cn(
                "inline-block h-1.5 w-1.5 rounded-full",
                query.enabled ? "bg-emerald-500" : "bg-muted-foreground",
              )}
            />
            <span className={query.enabled ? "text-foreground" : undefined}>
              {query.enabled ? "active" : "paused"}
            </span>
          </span>
          <QueryStatusBadge state={queueState} hasError={!!query.lastError} />
          <span className="bg-border h-3 w-px shrink-0" aria-hidden="true" />
          <span className="inline-flex items-center gap-1">
            <span>query:</span>
            <code className="bg-muted border-border text-foreground rounded border px-1.5 py-0.5 text-[11px]">
              {query.query}
            </code>
          </span>
          <span className="bg-border h-3 w-px shrink-0" aria-hidden="true" />
          <span className="inline-flex items-center gap-1">
            <span>last new:</span>
            <span className="text-foreground">{formatRelativeTime(query.lastNewResult)}</span>
          </span>
          {query.lastCount != null && (
            <>
              <span className="bg-border h-3 w-px shrink-0" aria-hidden="true" />
              <span className="inline-flex items-center gap-1">
                <span className="text-foreground font-semibold">{query.lastCount}</span>
                <span>results</span>
              </span>
            </>
          )}
        </div>
      </div>
      <div className="flex items-center gap-1">
        <Button
          variant="ghost"
          size="icon"
          title="Run now"
          onClick={() => runQuery.mutate(query.id)}
        >
          <Play className="size-4" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          title={query.enabled ? "Pause" : "Resume"}
          onClick={() => updateQuery.mutate({ enabled: !query.enabled })}
        >
          {query.enabled ? <Pause className="size-4" /> : <CirclePlay className="size-4" />}
        </Button>
        <Button variant="ghost" size="icon" title="Delete" onClick={handleDelete}>
          <Trash2 className="text-destructive size-4" />
        </Button>
      </div>
    </div>
  );
}
