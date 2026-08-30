import { createFileRoute } from "@tanstack/react-router";
import { AddQueryDialog } from "@/features/queries/components/AddQueryDialog";
import { QueryCard } from "@/features/queries/components/QueryCard";
import { useQueries, useQueueStatus } from "@/features/queries/hooks";
import { useSettings } from "@/features/settings/hooks";

export const Route = createFileRoute("/")({ component: QueryListPage });

function QueryListPage() {
  const queries = useQueries();
  const settings = useSettings();
  const queueStatus = useQueueStatus();

  if (queries.isPending || settings.isPending) {
    return <p className="text-muted-foreground text-sm">Loading…</p>;
  }
  if (queries.isError) {
    return <p className="text-destructive text-sm">{queries.error.message}</p>;
  }

  const defaultCron = settings.data?.defaultCron ?? "0 * * * *";

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-baseline gap-4">
        <h1 className="font-mono text-lg font-medium">Queries</h1>
        <span className="text-muted-foreground text-sm">{queries.data.length} configured</span>
        <div className="flex-1" />
        <AddQueryDialog defaultCron={defaultCron} />
      </div>

      {queries.data.length === 0 ? (
        <div className="text-muted-foreground py-16 text-center text-sm">
          No queries yet. Add one to start watching.
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {queries.data.map((q) => (
            <QueryCard
              key={q.id}
              query={q}
              queueState={queueStatus.data?.queries[String(q.id)]}
            />
          ))}
        </div>
      )}
    </div>
  );
}
