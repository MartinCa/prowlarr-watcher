import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ResultsTable } from "@/features/queries/components/ResultsTable";
import { useCreateQuery, useJob, useSearchPreview } from "@/features/queries/hooks";
import { cronGuruUrl, describeCron } from "@/lib/format";
import { ApiError } from "@/lib/api";

export function AddQueryDialog({ defaultCron }: { defaultCron: string }) {
  const [open, setOpen] = useState(false);
  const [queryText, setQueryText] = useState("");
  const [name, setName] = useState("");
  const [cron, setCron] = useState("");
  const [note, setNote] = useState("");
  const [jobId, setJobId] = useState<string>();

  const searchPreview = useSearchPreview();
  const job = useJob(jobId);
  const createQuery = useCreateQuery();

  const isPreviewLoading =
    searchPreview.isPending ||
    (job.data &&
      (job.data.status === "queued" ||
        job.data.status === "running" ||
        job.data.status === "retrying"));

  function reset() {
    setQueryText("");
    setName("");
    setCron("");
    setNote("");
    setJobId(undefined);
  }

  function handlePreview() {
    if (!queryText.trim()) return;
    searchPreview.mutate(queryText.trim(), {
      onSuccess: (data) => setJobId(data.jobId),
      onError: (error) => {
        toast.error(error instanceof ApiError ? error.message : "Failed to start preview search");
      },
    });
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    createQuery.mutate(
      {
        query: queryText.trim(),
        name: name.trim() || undefined,
        cron: cron.trim() || undefined,
        note: note.trim() || undefined,
      },
      {
        onSuccess: () => {
          setOpen(false);
          reset();
        },
        onError: (error) => {
          toast.error(error instanceof ApiError ? error.message : "Failed to create query");
        },
      },
    );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) reset();
      }}
    >
      <DialogTrigger render={<Button />}>+ Add Query</DialogTrigger>
      <DialogContent className="overflow-x-hidden sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>New Query</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="flex min-w-0 flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label htmlFor="query-input">Search Query</Label>
            <div className="flex gap-2">
              <Input
                id="query-input"
                value={queryText}
                onChange={(e) => setQueryText(e.target.value)}
                placeholder="e.g. ubuntu 24.04"
                required
                autoFocus
              />
              <Button
                type="button"
                variant="outline"
                onClick={handlePreview}
                disabled={Boolean(isPreviewLoading) || !queryText.trim()}
              >
                {isPreviewLoading ? "Searching…" : "Preview"}
              </Button>
            </div>
            <p className="text-muted-foreground text-xs">
              Also used as the display name. Results are seeded on first add — you&apos;ll only be
              notified about new results after that.
            </p>
          </div>

          <div className="flex flex-col gap-2">
            <Label htmlFor="query-name">Display Name (optional)</Label>
            <Input
              id="query-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Ubuntu ISOs"
            />
          </div>

          <div className="flex flex-col gap-2">
            <Label htmlFor="query-note">Note (optional)</Label>
            <Input
              id="query-note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="e.g. Only the remastered release"
            />
            <p className="text-muted-foreground text-xs">
              Shown as the first line of new-result notifications for this query.
            </p>
          </div>

          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <Label>Preview</Label>
              {job.data?.status === "done" && job.data.results && (
                <span className="text-muted-foreground text-xs">
                  {job.data.results.length} {job.data.results.length === 1 ? "result" : "results"}
                </span>
              )}
            </div>
            <div className="bg-muted/30 min-h-20 min-w-0 overflow-hidden rounded-md border">
              {job.data?.status === "error" && (
                <p className="text-destructive p-3 text-sm">Search failed: {job.data.error}</p>
              )}
              {job.data?.status === "done" && <ResultsTable results={job.data.results ?? []} />}
              {job.data &&
                (job.data.status === "queued" ||
                  job.data.status === "running" ||
                  job.data.status === "retrying") && (
                  <p className="text-muted-foreground p-3 text-sm">
                    {job.data.status === "queued" ? "Queued — waiting…" : "Searching Prowlarr…"}
                  </p>
                )}
              {!job.data && (
                <p className="text-muted-foreground p-3 text-sm">
                  Type a query above and click Preview.
                </p>
              )}
            </div>
          </div>

          <div className="flex flex-col gap-2">
            <Label htmlFor="query-cron">Cron Schedule (optional — default: {defaultCron})</Label>
            <Input
              id="query-cron"
              value={cron}
              onChange={(e) => setCron(e.target.value)}
              placeholder={defaultCron}
              className="max-w-56 font-mono"
            />
            <p className="text-muted-foreground text-xs">
              {describeCron(cron || defaultCron)} —{" "}
              <a
                href={cronGuruUrl(cron || defaultCron)}
                target="_blank"
                rel="noopener"
                className="hover:underline"
              >
                crontab.guru
              </a>
            </p>
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => setOpen(false)}
              className="w-full sm:w-auto"
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={!queryText.trim() || createQuery.isPending}
              className="w-full sm:w-auto"
            >
              Add Query
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
