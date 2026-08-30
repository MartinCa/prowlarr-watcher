import { Link } from "@tanstack/react-router";
import { CirclePlay, Pause, Play, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { QueryStatusBadge } from "@/features/queries/components/QueryStatusBadge";
import { useDeleteQuery, useRunQuery, useUpdateQuery } from "@/features/queries/hooks";
import { ApiError } from "@/lib/api";
import { formatRelativeTime } from "@/lib/format";
import type { Query } from "@/lib/types";

export function QueryCard({
  query,
  defaultCron,
  queueState,
}: {
  query: Query;
  defaultCron: string;
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
      className={`flex items-center justify-between gap-4 rounded-md border p-4 ${query.enabled ? "" : "opacity-50"}`}
    >
      <div>
        <Link
          to="/queries/$id"
          params={{ id: String(query.id) }}
          className="font-medium hover:underline"
        >
          {query.name}
        </Link>
        <div className="text-muted-foreground mt-1 flex flex-wrap items-center gap-3 font-mono text-xs">
          <span
            className={`inline-block h-1.5 w-1.5 rounded-full ${query.enabled ? "bg-green-500" : "bg-muted-foreground"}`}
          />
          <span>{query.enabled ? "active" : "paused"}</span>
          <QueryStatusBadge state={queueState} hasError={!!query.lastError} />
          <span>
            query: <span className="text-foreground">{query.query}</span>
          </span>
          <span>cron: {query.cron || defaultCron}</span>
          <span>last run: {formatRelativeTime(query.lastRun)}</span>
          {query.enabled && query.nextRun && <span>next: {formatRelativeTime(query.nextRun)}</span>}
          {query.lastCount != null && <span>{query.lastCount} results</span>}
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
