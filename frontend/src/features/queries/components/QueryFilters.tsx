import { BookOpen, Headphones, Sparkles } from "lucide-react";
import type { QueryFilterState } from "@/features/queries/filters";
import { cn } from "@/lib/utils";

const FILTERS = [
  { key: "newOnly", label: "New results", Icon: Sparkles },
  { key: "audiobook", label: "Audiobook", Icon: Headphones },
  { key: "ebook", label: "Ebook", Icon: BookOpen },
] as const;

/** Toggle chips narrowing the query list; all active chips must match. */
export function QueryFilters({
  value,
  onChange,
}: {
  value: QueryFilterState;
  onChange: (next: QueryFilterState) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filter queries">
      {FILTERS.map(({ key, label, Icon }) => (
        <button
          key={key}
          type="button"
          aria-pressed={value[key]}
          onClick={() => onChange({ ...value, [key]: !value[key] })}
          className={cn(
            "inline-flex h-6 items-center gap-1.5 rounded-4xl border px-2.5 text-xs font-medium transition-colors",
            value[key]
              ? "border-primary/40 bg-primary/10 text-primary"
              : "border-border text-muted-foreground hover:bg-muted",
          )}
        >
          <Icon className="size-3.5" aria-hidden="true" />
          {label}
        </button>
      ))}
    </div>
  );
}
