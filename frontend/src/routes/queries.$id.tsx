import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { IndexerChecklist } from "@/features/queries/components/IndexerChecklist";
import { StoredResultsTable } from "@/features/queries/components/ResultsTable";
import {
  useDeleteQuery,
  useQueryDetail,
  useRunQuery,
  useUpdateQuery,
} from "@/features/queries/hooks";
import { useSettings } from "@/features/settings/hooks";
import { ApiError } from "@/lib/api";
import {
  cronGuruUrl,
  describeCron,
  formatRelativeTime,
  prowlarrLinkBase,
  sanitizeUrl,
} from "@/lib/format";

export const Route = createFileRoute("/queries/$id")({ component: QueryDetailPage });

function QueryDetailPage() {
  const { id } = Route.useParams();
  const qid = Number(id);
  const navigate = useNavigate();
  const detail = useQueryDetail(qid);
  const settings = useSettings();
  const updateQuery = useUpdateQuery(qid);
  const deleteQuery = useDeleteQuery();
  const runQuery = useRunQuery();

  const [cronInput, setCronInput] = useState<string>();
  const [overrideEnabled, setOverrideEnabled] = useState<boolean>();
  const [excludedDraft, setExcludedDraft] = useState<number[]>();
  const [noteInput, setNoteInput] = useState<string>();

  if (detail.isPending || settings.isPending) {
    return <p className="text-muted-foreground text-sm">Loading…</p>;
  }
  if (detail.isError) {
    const status = detail.error instanceof ApiError ? detail.error.status : undefined;
    return (
      <p className="text-destructive text-sm">
        {status === 404 ? "Query not found." : detail.error.message}
      </p>
    );
  }

  const query = detail.data;
  const defaultCron = settings.data?.defaultCron ?? "0 * * * *";
  const prowlarrBase = settings.data ? prowlarrLinkBase(settings.data) : "";
  const cron = cronInput ?? query.cron ?? "";
  const override = overrideEnabled ?? query.excludedIndexers !== null;
  const excluded = excludedDraft ?? query.excludedIndexers ?? [];
  const note = noteInput ?? query.note ?? "";
  const newCount = query.results.filter((r) => r.isNew).length;

  function handleDelete() {
    if (!confirm("Delete this query and all its results?")) return;
    deleteQuery.mutate(qid, {
      onSuccess: () => {
        toast.success("Query deleted");
        void navigate({ to: "/" });
      },
      onError: (error) => {
        // A 404 means it was already deleted (e.g. deleted elsewhere); leave the detail page anyway.
        const alreadyGone = error instanceof ApiError && error.status === 404;
        if (alreadyGone) {
          void navigate({ to: "/" });
        } else {
          toast.error(error instanceof ApiError ? error.message : "Delete failed");
        }
      },
    });
  }

  function saveCron() {
    updateQuery.mutate(
      { cron: cronInput?.trim() || null },
      {
        onSuccess: () => toast.success("Schedule saved"),
        onError: () => toast.error("Failed to save schedule"),
      },
    );
  }

  function saveNote() {
    updateQuery.mutate(
      { note: noteInput?.trim() || null },
      {
        onSuccess: () => toast.success("Note saved"),
        onError: () => toast.error("Failed to save note"),
      },
    );
  }

  function saveIndexers() {
    updateQuery.mutate(
      { excludedIndexers: override ? excluded : null },
      {
        onSuccess: () => toast.success("Indexer exclusions saved"),
        onError: () => toast.error("Failed to save indexer exclusions"),
      },
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <Link to="/" className="text-muted-foreground text-sm hover:underline">
          Queries
        </Link>
        <span className="text-muted-foreground mx-2">/</span>
        <span className="font-mono text-lg font-medium">{query.name}</span>
      </div>

      <Card className="flex flex-row flex-wrap items-start gap-8 p-5">
        <Field label="Search query">
          <code className="text-sm">{query.query}</code>
          {prowlarrBase &&
            sanitizeUrl(`${prowlarrBase}/search?query=${encodeURIComponent(query.query)}`) && (
              <a
                href={sanitizeUrl(
                  `${prowlarrBase}/search?query=${encodeURIComponent(query.query)}`,
                )!}
                target="_blank"
                rel="noopener noreferrer"
                title="Search in Prowlarr"
                className="text-muted-foreground ml-2 text-xs hover:underline"
              >
                ↗ Prowlarr
              </a>
            )}
        </Field>
        <Field label="Status">
          <span
            className={`inline-block h-1.5 w-1.5 rounded-full ${query.enabled ? "bg-status-ok" : "bg-muted-foreground"}`}
          />{" "}
          {query.enabled ? "Active" : "Paused"}
          {query.lastError && (
            <Badge
              variant="outline"
              className="border-destructive/40 bg-destructive/10 text-destructive ml-2"
            >
              Error
            </Badge>
          )}
        </Field>
        <Field label="Last run">{formatRelativeTime(query.lastRun)}</Field>
        {query.lastError && <Field label="Last error">{query.lastError}</Field>}
        <Field label="Next run">
          {query.enabled && query.nextRun ? formatRelativeTime(query.nextRun) : "—"}
        </Field>
        <Field label="Total results">{query.results.length}</Field>
        <div className="ml-auto flex items-center gap-2">
          <Button size="sm" onClick={() => runQuery.mutate(qid)}>
            Run Now
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => updateQuery.mutate({ enabled: !query.enabled })}
          >
            {query.enabled ? "Pause" : "Resume"}
          </Button>
          <Button size="sm" variant="outline" className="text-destructive" onClick={handleDelete}>
            Delete
          </Button>
        </div>
      </Card>

      <Card className="flex flex-col gap-3 p-5">
        <h2 className="text-muted-foreground font-mono text-xs uppercase">Schedule</h2>
        <div className="flex items-center gap-2">
          <Input
            className="max-w-56 font-mono"
            value={cron}
            placeholder={defaultCron}
            onChange={(e) => setCronInput(e.target.value)}
          />
          <Button size="sm" variant="outline" onClick={saveCron}>
            Save
          </Button>
          <span className="text-muted-foreground text-xs">
            {query.cron
              ? `Override active — default: ${defaultCron}`
              : `Using default: ${defaultCron}`}
          </span>
        </div>
        <p className="text-sm">
          {describeCron(cron || defaultCron)} —{" "}
          <a
            href={cronGuruUrl(cron || defaultCron)}
            target="_blank"
            rel="noopener"
            className="text-muted-foreground hover:underline"
          >
            crontab.guru
          </a>
        </p>
      </Card>

      <Card className="flex flex-col gap-3 p-5">
        <h2 className="text-muted-foreground font-mono text-xs uppercase">Note</h2>
        <div className="flex items-center gap-2">
          <Input
            value={note}
            placeholder="e.g. Only the remastered release"
            onChange={(e) => setNoteInput(e.target.value)}
          />
          <Button size="sm" variant="outline" onClick={saveNote}>
            Save
          </Button>
        </div>
        <p className="text-muted-foreground text-xs">
          Shown as the first line of new-result notifications for this query.
        </p>
      </Card>

      <Card className="flex flex-col gap-3 p-5">
        <h2 className="text-muted-foreground font-mono text-xs uppercase">Indexers</h2>
        <div className="flex items-center gap-2">
          <Checkbox
            id="override-indexers"
            checked={override}
            onCheckedChange={(checked) => setOverrideEnabled(checked === true)}
          />
          <Label htmlFor="override-indexers" className="font-normal">
            Override the default exclusion list for this query
          </Label>
        </div>
        <IndexerChecklist excluded={excluded} onChange={setExcludedDraft} disabled={!override} />
        <p className="text-muted-foreground text-xs">
          {override
            ? "Only the checked indexers above are excluded for this query."
            : "Using the default exclusion list from Settings."}
        </p>
        <div>
          <Button size="sm" variant="outline" onClick={saveIndexers}>
            Save
          </Button>
        </div>
      </Card>

      <div className="flex items-center gap-3">
        <h2 className="text-muted-foreground font-mono text-xs uppercase">
          Results ({query.results.length})
        </h2>
        {newCount > 0 && (
          <Badge variant="outline" className="border-primary/40 bg-primary/10 text-primary">
            {newCount} new
          </Badge>
        )}
      </div>
      <StoredResultsTable results={query.results} />
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-muted-foreground mb-1 font-mono text-[10px] tracking-wide uppercase">
        {label}
      </div>
      <div className="text-sm">{children}</div>
    </div>
  );
}
