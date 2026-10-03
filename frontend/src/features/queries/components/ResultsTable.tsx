import { useEffect, useRef, useState } from "react";
import { EllipsisIcon, HardDriveDownloadIcon } from "lucide-react";
import { ActionButton } from "@/components/action-button";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { deriveGrabStatus } from "@/features/queries/grab-status";
import { useGrabResult, useJob } from "@/features/queries/hooks";
import { formatRelativeTime, formatSize, sanitizeUrl } from "@/lib/format";
import { notifications } from "@/lib/notifications";
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
 * from the indexer first, so this is a queued job: poll it. The icon tints green or red until
 * the next click (or until the row unmounts); the answer from Prowlarr / the client goes in a toast.
 */
function GrabButton({ qid, result }: { qid: number; result: Result }) {
  const grab = useGrabResult(qid);
  const [jobId, setJobId] = useState<string>();
  const job = useJob(jobId);
  const notifiedJobId = useRef<string>(undefined);

  const jobStatus = job.data?.status;
  const jobFailed = job.isError || jobStatus === "error";
  const status = deriveGrabStatus({
    requestPending: grab.isPending,
    requestFailed: grab.isError,
    jobId,
    jobStatus,
    pollFailed: job.isError,
  });

  const failureText = grab.isError
    ? grab.error.message
    : job.isError
      ? job.error.message
      : (job.data?.error ?? "Grab failed");
  const successText = job.data?.message ?? "Sent to the download client";

  // One toast per finished job: the poll keeps returning the same terminal state, and a
  // refetch must not announce it again.
  useEffect(() => {
    if (!jobId || notifiedJobId.current === jobId) return;
    if (jobStatus === "done") {
      notifiedJobId.current = jobId;
      notifications.success(successText, { description: result.title ?? undefined });
    } else if (jobFailed) {
      notifiedJobId.current = jobId;
      notifications.error("Grab failed", {
        description: [result.title, failureText].filter(Boolean).join(" — "),
      });
    }
  }, [jobId, jobStatus, jobFailed, successText, failureText, result.title]);

  function handleGrab() {
    setJobId(undefined);
    grab.mutate(result.id, {
      onSuccess: (data) => setJobId(data.jobId),
      onError: (error) =>
        notifications.error("Grab failed", {
          description: [result.title, error.message].filter(Boolean).join(" — "),
        }),
    });
  }

  return (
    <ActionButton
      icon={HardDriveDownloadIcon}
      label={
        result.guid
          ? "Send to the download client via Prowlarr"
          : "This result has no GUID to grab it by"
      }
      status={status}
      resultLabel={status === "success" ? successText : failureText}
      disabled={!result.guid}
      onClick={handleGrab}
    />
  );
}

/** Rarely-used row actions, kept out of the way so the Grab column stays one icon wide. */
function RowActions({ result, onClear }: { result: Result; onClear: (result: Result) => void }) {
  const safeDownloadUrl = sanitizeUrl(result.downloadUrl);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={<Button variant="ghost" size="icon-sm" aria-label="More actions" />}
      >
        <EllipsisIcon aria-hidden />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-auto">
        {safeDownloadUrl && (
          <DropdownMenuItem
            render={
              <a
                href={safeDownloadUrl}
                target="_blank"
                rel="noopener noreferrer"
                title="Download the release directly"
              />
            }
          >
            Download
          </DropdownMenuItem>
        )}
        {/* No confirm: clearing one row is cheap and undone by the next run re-finding it. */}
        <DropdownMenuItem
          title="Clear this result so it is notified about again"
          onClick={() => onClear(result)}
        >
          Clear
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
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
                <RowActions result={r} onClear={onClear} />
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
