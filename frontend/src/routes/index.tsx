import { createFileRoute } from "@tanstack/react-router";
import { Search, X } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { AddQueryDialog } from "@/features/queries/components/AddQueryDialog";
import { QueryCard } from "@/features/queries/components/QueryCard";
import { useQueries, useQueueStatus } from "@/features/queries/hooks";
import { useSettings } from "@/features/settings/hooks";
import type { Query } from "@/lib/types";

export const Route = createFileRoute("/")({ component: QueryListPage });

function matchesQuery(q: Query, search: string): boolean {
  const trimmed = search.trim().toLowerCase();
  if (!trimmed) return true;

  const name = q.name.toLowerCase();
  const query = q.query.toLowerCase();
  const note = q.note ? q.note.toLowerCase() : "";

  if (name.includes(trimmed) || query.includes(trimmed) || note.includes(trimmed)) {
    return true;
  }

  const tokens = trimmed.split(/\s+/).filter(Boolean);
  if (tokens.length > 1) {
    return tokens.every((tok) => name.includes(tok) || query.includes(tok) || note.includes(tok));
  }

  return false;
}

function QueryListPage() {
  const queries = useQueries();
  const settings = useSettings();
  const queueStatus = useQueueStatus();
  const [search, setSearch] = useState("");

  if (queries.isPending || settings.isPending) {
    return <p className="text-muted-foreground text-sm">Loading…</p>;
  }
  if (queries.isError) {
    return <p className="text-destructive text-sm">{queries.error.message}</p>;
  }

  const defaultCron = settings.data?.defaultCron ?? "0 * * * *";
  const filteredQueries = queries.data.filter((q) => matchesQuery(q, search));
  const isFiltering = search.trim().length > 0;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-baseline gap-4">
        <h1 className="font-mono text-lg font-medium">Queries</h1>
        <span className="text-muted-foreground text-sm">
          {isFiltering
            ? `${filteredQueries.length} of ${queries.data.length} matching`
            : `${queries.data.length} configured`}
        </span>
        <div className="flex-1" />
        <AddQueryDialog defaultCron={defaultCron} />
      </div>

      {queries.data.length === 0 ? (
        <div className="text-muted-foreground py-16 text-center text-sm">
          No queries yet. Add one to start watching.
        </div>
      ) : (
        <>
          <div className="relative">
            <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2" />
            <Input
              type="search"
              placeholder="Search by name, query, or note…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape" && search) {
                  setSearch("");
                  e.preventDefault();
                }
              }}
              className="pr-8 pl-8 [&::-webkit-search-cancel-button]:appearance-none"
              aria-label="Search queries"
            />
            {search && (
              <Button
                type="button"
                variant="ghost"
                size="icon-xs"
                className="text-muted-foreground hover:text-foreground absolute top-1/2 right-1.5 -translate-y-1/2"
                onClick={() => setSearch("")}
                aria-label="Clear search"
              >
                <X />
              </Button>
            )}
          </div>

          {filteredQueries.length === 0 ? (
            <div className="text-muted-foreground flex flex-col items-center justify-center gap-3 py-16 text-center text-sm">
              <p>No queries match &ldquo;{search}&rdquo;</p>
              <Button variant="outline" size="sm" onClick={() => setSearch("")}>
                Clear search
              </Button>
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              {filteredQueries.map((q) => (
                <QueryCard
                  key={q.id}
                  query={q}
                  queueState={queueStatus.data?.queries[String(q.id)]}
                />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
