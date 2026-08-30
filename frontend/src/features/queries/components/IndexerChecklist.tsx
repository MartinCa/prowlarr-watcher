import { useIndexers } from "@/features/queries/hooks";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";

export function IndexerChecklist({
  excluded,
  onChange,
  disabled = false,
}: {
  excluded: number[];
  onChange: (excluded: number[]) => void;
  disabled?: boolean;
}) {
  const { data, isPending, isError, error } = useIndexers();

  if (isPending) {
    return <p className="text-muted-foreground text-sm">Loading indexers…</p>;
  }
  if (isError) {
    return <p className="text-destructive text-sm">{error.message}</p>;
  }
  if (data.indexers.length === 0) {
    return <p className="text-muted-foreground text-sm">No indexers configured in Prowlarr.</p>;
  }

  function toggle(id: number, checked: boolean) {
    onChange(checked ? [...excluded, id] : excluded.filter((x) => x !== id));
  }

  return (
    <div className="flex flex-col gap-2">
      {data.indexers.map((indexer) => (
        <div key={indexer.id} className="flex items-center gap-2">
          <Checkbox
            id={`indexer-${indexer.id}`}
            checked={excluded.includes(indexer.id)}
            disabled={disabled}
            onCheckedChange={(checked) => toggle(indexer.id, checked === true)}
          />
          <Label htmlFor={`indexer-${indexer.id}`} className="font-normal">
            {indexer.name}
          </Label>
        </div>
      ))}
    </div>
  );
}
