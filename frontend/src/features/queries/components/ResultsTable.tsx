import { Check, Copy, ExternalLink } from "lucide-react";
import { useState } from "react";
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
import { copyText } from "@/lib/clipboard";
import { formatRelativeTime, formatSize, sanitizeUrl } from "@/lib/format";
import type { PreviewResult, Result } from "@/lib/types";
import { cn } from "@/lib/utils";

function TrackerLinkIcon({ href }: { href: string | null }) {
  if (!href) return null;
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      title="Open at tracker"
      aria-label="Open at tracker"
      className="text-muted-foreground hover:bg-muted hover:text-foreground inline-flex items-center justify-center rounded-lg p-1.5"
    >
      <ExternalLink className="size-3.5" />
    </a>
  );
}

function CopyLinkButton({ href }: { href: string | null }) {
  const [copied, setCopied] = useState(false);
  if (!href) return null;
  const url = href;
  function handleCopy() {
    void copyText(url).then((ok) => {
      if (ok) {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }
    });
  }
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      title={copied ? "Copied!" : "Copy link"}
      aria-label={copied ? "Copied!" : "Copy link"}
      onClick={handleCopy}
    >
      {copied ? <Check className="text-status-ok" /> : <Copy />}
    </Button>
  );
}

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
                  <span className="flex min-w-0 items-center gap-1.5">
                    <span className="min-w-0 flex-1 truncate">{r.title ?? "—"}</span>
                    {safeInfoUrl && (
                      <span className="flex shrink-0 items-center gap-0.5">
                        <TrackerLinkIcon href={safeInfoUrl} />
                        <CopyLinkButton href={safeInfoUrl} />
                      </span>
                    )}
                  </span>
                </TableCell>
                <TableCell className="truncate">
                  <Badge variant="outline" className="max-w-full truncate">
                    {r.indexer ?? "—"}
                  </Badge>
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

export function StoredResultsTable({ results }: { results: Result[] }) {
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
          <TableHead>Indexer</TableHead>
          <TableHead>Size</TableHead>
          <TableHead>Seeders</TableHead>
          <TableHead>First seen</TableHead>
          <TableHead className="w-20 text-right">Link</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {results.map((r) => {
          const safeInfoUrl = sanitizeUrl(r.infoUrl);
          const safeDownloadUrl = sanitizeUrl(r.downloadUrl);
          return (
            <TableRow key={r.id} className={r.isNew ? "border-l-primary border-l-2" : undefined}>
              <TableCell>
                {r.isNew && (
                  <Badge variant="outline" className="border-primary/40 bg-primary/10 text-primary">
                    New
                  </Badge>
                )}
              </TableCell>
              <TableCell className="max-w-96 truncate">
                {safeInfoUrl ? (
                  <a
                    href={safeInfoUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="hover:underline"
                  >
                    {r.title ?? "—"}
                  </a>
                ) : (
                  (r.title ?? "—")
                )}
                {safeDownloadUrl && (
                  <a
                    href={safeDownloadUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-muted-foreground ml-2 text-xs hover:underline"
                    title="Download"
                  >
                    ↓
                  </a>
                )}
              </TableCell>
              <TableCell>
                <Badge variant="outline">{r.indexer ?? "—"}</Badge>
              </TableCell>
              <TableCell className="font-mono whitespace-nowrap">{formatSize(r.size)}</TableCell>
              <TableCell className={seederColor(r.seeders)}>{r.seeders ?? "—"}</TableCell>
              <TableCell className="text-muted-foreground font-mono text-xs whitespace-nowrap">
                {formatRelativeTime(r.firstSeen)}
              </TableCell>
              <TableCell>
                <div className="flex items-center justify-end gap-0.5">
                  <TrackerLinkIcon href={safeInfoUrl} />
                  <CopyLinkButton href={safeInfoUrl} />
                </div>
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
