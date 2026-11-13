import { BookOpen, Headphones, Sparkles } from "lucide-react";
import {
  nextMediaFilter,
  type MediaFilter,
  type QueryFilterState,
} from "@/features/queries/filters";
import { cn } from "cn";

const MEDIA_FILTERS = [
  { key: "audiobook", label: "Audiobook", Icon: Headphones },
  { key: "ebook", label: "Ebook", Icon: BookOpen },
] as const;

const CHIP_BASE =
  "inline-flex h-6 items-center gap-1.5 rounded-4xl border px-2.5 text-xs font-medium transition-colors";
const CHIP_OFF = "border-border text-muted-foreground hover:bg-muted";
const CHIP_INCLUDE = "border-primary/40 bg-primary/10 text-primary";
const CHIP_EXCLUDE = "border-destructive/40 bg-destructive/10 text-destructive";

function mediaChipClass(state: MediaFilter): string {
  if (state === "include") return CHIP_INCLUDE;
  if (state === "exclude") return CHIP_EXCLUDE;
  return CHIP_OFF;
}

/**
 * Chips narrowing the query list; all active chips must match. Media chips cycle
 * off → include → exclude ("NOT") → off on repeated clicks.
 */
export function QueryFilters({
  value,
  onChange,
}: {
  value: QueryFilterState;
  onChange: (next: QueryFilterState) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filter queries">
      <button
        type="button"
        aria-pressed={value.newOnly}
        onClick={() => onChange({ ...value, newOnly: !value.newOnly })}
        className={cn(CHIP_BASE, value.newOnly ? CHIP_INCLUDE : CHIP_OFF)}
      >
        <Sparkles className="size-3.5" aria-hidden="true" />
        New results
      </button>
      {MEDIA_FILTERS.map(({ key, label, Icon }) => {
        const state = value[key];
        return (
          <button
            key={key}
            type="button"
            aria-pressed={state === "off" ? false : state === "include" ? true : "mixed"}
            aria-label={state === "exclude" ? `Not ${label}` : label}
            title={
              state === "off"
                ? `Click to show only ${label.toLowerCase()}`
                : state === "include"
                  ? `Click to exclude ${label.toLowerCase()}`
                  : `Click to clear ${label.toLowerCase()} filter`
            }
            onClick={() => onChange({ ...value, [key]: nextMediaFilter(state) })}
            className={cn(CHIP_BASE, mediaChipClass(state))}
          >
            <Icon className="size-3.5" aria-hidden="true" />
            <span className={cn(state === "exclude" && "line-through")}>
              {state === "exclude" ? `Not ${label}` : label}
            </span>
          </button>
        );
      })}
    </div>
  );
}
