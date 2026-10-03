import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useGrabResult, useJob } from "@/features/queries/hooks";
import { ApiError } from "@/lib/api";
import { formatRelativeTime, formatSize, sanitizeUrl } from "@/lib/format";
import type { PreviewResult, Result } from "@/lib/types";
import { cn } from "@/lib/utils";

function seederColor(seeders: number | null | undefined): string {
  if (seeders == null) return "text-muted-foreground";
  if (seeders > 10) return "text-status-ok";
  if (seeders > 0) return "text-status-warn";
  return "text-status-error";
}

export function ResultsTable({ results }: { results: PreviewResult[] }) {
  if (results.length === 0) {
    return <p className="text-muted-foreground p-3 text-sm">No results found for this query.</p>;
  }
  return (
    <div className="preview-table">
      <Table className="table-fixed">
        <TableHeader className="bg-popover sticky top-0 z-10">
          <TableRow>
            <TableHead className="bg-popover">Title</TableHead>
            <TableHead className="bg-popover w-28 sm:w-32">Indexer</TableHead>
            <TableHead className="bg-popover w-20 text-right sm:w-24">Size</TableHead>
            <TableHead className="bg-popover w-14 text-right sm:w-16">Seeds</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {results.map((r, i) => {
            const safeInfoUrl = sanitizeUrl(r.infoUrl);
            return (
              <TableRow key={r.guid ?? i}>
                <TableCell className="truncate" title={r.title ?? undefined}>
                  {r.title ?? "—"}
                </TableCell>
                <TableCell className="truncate">
                  {safeInfoUrl ? (
                    <Badge
                      variant="outline"
                      className="max-w-full truncate"
                      render={
                        <a
                          href={safeInfoUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          title="Open at tracker"
                          aria-label={`Open at ${r.indexer ?? "tracker"}`}
                        />
                      }
                    >
                      {r.indexer ?? "—"}
                    </Badge>
                  ) : (
                    <Badge variant="outline" className="max-w-full truncate">
                      {r.indexer ?? "—"}
                    </Badge>
                  )}
                </TableCell>
                <TableCell className="text-right font-mono whitespace-nowrap">
                  {formatSize(r.size)}
                </TableCell>
                <TableCell className={cn("text-right", seederColor(r.seeders))}>
                  {r.seeders ?? "—"}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

/**
 * Sends a result to the download client through Prowlarr. The server refreshes the release
 * from the indexer first, so this is a queued job: poll it and show Prowlarr's / the client's
 * answer next to the button.
 */
function GrabButton({ qid, result }: { qid: number; result: Result }) {
  const grab = useGrabResult(qid);
  const [jobId, setJobId] = useState<string>();
  const job = useJob(jobId);

  const status = job.data?.status;
  const inFlight =
    grab.isPending ||
    (!!jobId && !job.isError && (!status || ["queued", "running", "retrying"].includes(status)));

  let outcome: { ok: boolean; text: string } | undefined;
  if (grab.isError) {
    outcome = { ok: false, text: grab.error.message };
  } else if (job.isError) {
    outcome = { ok: false, text: job.error.message };
  } else if (status === "done") {
    outcome = { ok: true, text: job.data?.message ?? "Sent to the download client" };
  } else if (status === "error") {
    outcome = { ok: false, text: job.data?.error ?? "Grab failed" };
  }

  function handleGrab() {
    setJobId(undefined);
    grab.mutate(result.id, {
      onSuccess: (data) => setJobId(data.jobId),
      onError: (error) => toast.error(error instanceof ApiError ? error.message : "Grab failed"),
    });
  }

  return (
    <div className="flex min-w-24 flex-col items-start gap-1">
      <Button
        size="sm"
        variant="outline"
        disabled={inFlight || !result.guid}
        title={
          result.guid
            ? "Send to the download client via Prowlarr"
            : "This result has no GUID to grab it by"
        }
        onClick={handleGrab}
      >
        {inFlight ? "Grabbing…" : "Grab"}
      </Button>
      {outcome && (
        <span
          role="status"
          className={cn(
            "max-w-48 text-xs break-words whitespace-normal",
            outcome.ok ? "text-status-ok" : "text-status-error",
          )}
        >
          {outcome.ok ? "✓ " : "✗ "}
          {outcome.text}
        </span>
      )}
    </div>
  );
}

/** Title that stays one truncated line until clicked, so long titles are readable on mobile. */
function ExpandableTitle({ title }: { title: string | null }) {
  const [expanded, setExpanded] = useState(false);
  if (!title) return <>—</>;
  return (
    <button
      type="button"
      aria-expanded={expanded}
      title={expanded ? "Click to collapse" : title}
      onClick={() => setExpanded((v) => !v)}
      className={cn(
        "block max-w-full cursor-pointer text-left",
        expanded ? "break-words whitespace-normal" : "truncate",
      )}
    >
      {title}
    </button>
  );
}

export function StoredResultsTable({
  qid,
  results,
  onClear,
}: {
  qid: number;
  results: Result[];
  onClear: (result: Result) => void;
}) {
  if (results.length === 0) {
    return (
      <p className="text-muted-foreground py-10 text-center text-sm">
        No results yet — run the query to populate.
      </p>
    );
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead></TableHead>
          <TableHead>Title</TableHead>
          <TableHead>Grab</TableHead>
          <TableHead>Indexer</TableHead>
          <TableHead>Size</TableHead>
          <TableHead>Seeders</TableHead>
          <TableHead>First seen</TableHead>
          <TableHead></TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {results.map((r) => {
          const safeInfoUrl = sanitizeUrl(r.infoUrl);
          return (
            <TableRow key={r.id} className={r.isNew ? "border-l-primary border-l-2" : undefined}>
              <TableCell>
                {r.isNew && (
                  <Badge variant="outline" className="border-primary/40 bg-primary/10 text-primary">
                    New
                  </Badge>
                )}
              </TableCell>
              <TableCell className="max-w-48 sm:max-w-96">
                <ExpandableTitle title={r.title} />
              </TableCell>
              <TableCell>
                <GrabButton qid={qid} result={r} />
              </TableCell>
              <TableCell>
                {safeInfoUrl ? (
                  <Badge
                    variant="outline"
                    render={
                      <a
                        href={safeInfoUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        title="Open at tracker"
                        aria-label={`Open at ${r.indexer ?? "tracker"}`}
                      />
                    }
                  >
                    {r.indexer ?? "—"}
                  </Badge>
                ) : (
                  <Badge variant="outline">{r.indexer ?? "—"}</Badge>
                )}
              </TableCell>
              <TableCell className="font-mono whitespace-nowrap">{formatSize(r.size)}</TableCell>
              <TableCell className={seederColor(r.seeders)}>{r.seeders ?? "—"}</TableCell>
              <TableCell className="text-muted-foreground font-mono text-xs whitespace-nowrap">
                {formatRelativeTime(r.firstSeen)}
              </TableCell>
              <TableCell>
                {/* No confirm: clearing one row is cheap and undone by the next run re-finding it. */}
                <Button
                  size="sm"
                  variant="ghost"
                  title="Clear this result so it is notified about again"
                  onClick={() => onClear(r)}
                >
                  Clear
                </Button>
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
