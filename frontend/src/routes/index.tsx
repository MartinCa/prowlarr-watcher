import { createFileRoute } from "@tanstack/react-router";
import { Search, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { AddQueryDialog } from "@/features/queries/components/AddQueryDialog";
import { ClearIndexerResults } from "@/features/queries/components/ClearIndexerResults";
import { QueryFilters } from "@/features/queries/components/QueryFilters";
import { hasActiveFilter, NO_FILTERS, type QueryFilterState } from "@/features/queries/filters";
import { QueryCard } from "@/features/queries/components/QueryCard";
import { useMarkAllSeen, useQueries, useQueueStatus } from "@/features/queries/hooks";
import { useSettings } from "@/features/settings/hooks";
import { ApiError } from "@/lib/api";
import type { Query } from "@/lib/types";

export const Route = createFileRoute("/")({ component: QueryListPage });

function matchesFilters(q: Query, filters: QueryFilterState): boolean {
  if (filters.newOnly && q.newCount === 0) return false;
  if (filters.audiobook && !q.audiobook) return false;
  if (filters.ebook && !q.ebook) return false;
  return true;
}

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
  const markAllSeen = useMarkAllSeen();
  const [search, setSearch] = useState("");
  const [filters, setFilters] = useState<QueryFilterState>(NO_FILTERS);

  if (queries.isPending || settings.isPending) {
    return <p className="text-muted-foreground text-sm">Loading…</p>;
  }
  if (queries.isError) {
    return <p className="text-destructive text-sm">{queries.error.message}</p>;
  }

  const defaultCron = settings.data?.defaultCron ?? "0 * * * *";
  const filteredQueries = queries.data.filter(
    (q) => matchesQuery(q, search) && matchesFilters(q, filters),
  );
  const isFiltering = search.trim().length > 0 || hasActiveFilter(filters);
  const totalNew = queries.data.reduce((sum, q) => sum + q.newCount, 0);

  function handleMarkAllSeen() {
    markAllSeen.mutate(undefined, {
      onSuccess: (data) => toast.success(`Cleared ${data.cleared} new results`),
      onError: (error) =>
        toast.error(error instanceof ApiError ? error.message : "Failed to clear new results"),
    });
  }

  function clearSearchAndFilters() {
    setSearch("");
    setFilters(NO_FILTERS);
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-2">
        <h1 className="font-mono text-lg font-medium">Queries</h1>
        <span className="text-muted-foreground text-sm">
          {isFiltering
            ? `${filteredQueries.length} of ${queries.data.length} matching`
            : `${queries.data.length} configured`}
        </span>
        <div className="flex-1" />
        <Button
          variant="outline"
          size="sm"
          disabled={totalNew === 0 || markAllSeen.isPending}
          onClick={handleMarkAllSeen}
          title="Clear the new-result indication on all queries"
        >
          Clear new
        </Button>
        <ClearIndexerResults />
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

          <QueryFilters value={filters} onChange={setFilters} />

          {filteredQueries.length === 0 ? (
            <div className="text-muted-foreground flex flex-col items-center justify-center gap-3 py-16 text-center text-sm">
              <p>
                {search.trim()
                  ? `No queries match \u201c${search}\u201d with the current filters`
                  : "No queries match the current filters"}
              </p>
              <Button variant="outline" size="sm" onClick={clearSearchAndFilters}>
                Clear search and filters
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
