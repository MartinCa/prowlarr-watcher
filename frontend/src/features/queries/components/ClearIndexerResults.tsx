import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { useClearIndexerResults, useResultIndexers } from "@/features/queries/hooks";
import { ApiError } from "@/lib/api";

/** Forgets every stored result from one indexer, across all queries, so they notify again. */
export function ClearIndexerResults() {
  const indexers = useResultIndexers();
  const clear = useClearIndexerResults();
  const [selected, setSelected] = useState("");

  const options = indexers.data?.indexers ?? [];
  const current = options.find((i) => i.name === selected);

  function handleClear() {
    if (!current) return;
    if (
      !confirm(
        `Clear all ${current.count} stored results from "${current.name}" across all queries? They will notify again if seen.`,
      )
    )
      return;
    clear.mutate(current.name, {
      onSuccess: (data) => {
        toast.success(`Cleared ${data.deleted} results from ${current.name}`);
        setSelected("");
      },
      onError: (error) => toast.error(error instanceof ApiError ? error.message : "Clear failed"),
    });
  }

  return (
    <div className="flex items-center gap-2">
      <select
        aria-label="Indexer to clear results from"
        className="border-input bg-background h-8 rounded-md border px-2 text-xs"
        value={current ? selected : ""}
        disabled={options.length === 0}
        onChange={(e) => setSelected(e.target.value)}
      >
        <option value="">Clear results from indexer…</option>
        {options.map((i) => (
          <option key={i.name} value={i.name}>
            {i.name} ({i.count})
          </option>
        ))}
      </select>
      <Button
        size="sm"
        variant="outline"
        disabled={!current || clear.isPending}
        onClick={handleClear}
      >
        Clear
      </Button>
    </div>
  );
}
