import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatRelativeTime, formatSize } from "@/lib/format";
import type { PreviewResult, Result } from "@/lib/types";
import { cn } from "@/lib/utils";

function seederColor(seeders: number | null | undefined): string {
  if (seeders == null) return "text-muted-foreground";
  if (seeders > 10) return "text-green-600";
  if (seeders > 0) return "text-amber-600";
  return "text-destructive";
}

export function ResultsTable({ results }: { results: PreviewResult[] }) {
  if (results.length === 0) {
    return <p className="text-muted-foreground p-3 text-sm">No results found for this query.</p>;
  }
  return (
    <div className="preview-table">
      <Table className="table-fixed">
        <TableHeader className="sticky top-0 z-10 bg-popover">
          <TableRow>
            <TableHead className="bg-popover">Title</TableHead>
            <TableHead className="bg-popover w-28 sm:w-32">Indexer</TableHead>
            <TableHead className="bg-popover w-20 text-right sm:w-24">Size</TableHead>
            <TableHead className="bg-popover w-14 text-right sm:w-16">Seeds</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {results.map((r, i) => (
            <TableRow key={r.guid ?? i}>
              <TableCell className="truncate" title={r.title ?? undefined}>
                {r.title ?? "—"}
              </TableCell>
              <TableCell className="truncate">
                <Badge variant="outline" className="max-w-full truncate">
                  {r.indexer ?? "—"}
                </Badge>
              </TableCell>
              <TableCell className="font-mono text-right whitespace-nowrap">
                {formatSize(r.size)}
              </TableCell>
              <TableCell className={cn("text-right", seederColor(r.seeders))}>
                {r.seeders ?? "—"}
              </TableCell>
            </TableRow>
          ))}
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
        </TableRow>
      </TableHeader>
      <TableBody>
        {results.map((r) => (
          <TableRow key={r.id} className={r.isNew ? "border-l-primary border-l-2" : undefined}>
            <TableCell>
              {r.isNew && (
                <Badge variant="outline" className="border-primary/40 bg-primary/10 text-primary">
                  New
                </Badge>
              )}
            </TableCell>
            <TableCell className="max-w-96 truncate">
              {r.infoUrl ? (
                <a href={r.infoUrl} target="_blank" rel="noopener" className="hover:underline">
                  {r.title ?? "—"}
                </a>
              ) : (
                (r.title ?? "—")
              )}
              {r.downloadUrl && (
                <a
                  href={r.downloadUrl}
                  target="_blank"
                  rel="noopener"
                  className="text-muted-foreground ml-2 text-xs hover:underline"
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
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
