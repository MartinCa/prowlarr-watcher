import { BookOpen, Headphones } from "lucide-react";
import { cn } from "@/lib/utils";

const MEDIA_TYPES = [
  { key: "audiobook", label: "Audiobook", Icon: Headphones },
  { key: "ebook", label: "Ebook", Icon: BookOpen },
] as const;

type MediaKey = (typeof MEDIA_TYPES)[number]["key"];

/** Toggleable Audiobook / Ebook badges. */
export function MediaTypeToggles({
  value,
  onChange,
  disabled,
}: {
  value: Record<MediaKey, boolean>;
  onChange: (key: MediaKey, next: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {MEDIA_TYPES.map(({ key, label, Icon }) => (
        <button
          key={key}
          type="button"
          aria-pressed={value[key]}
          disabled={disabled}
          onClick={() => onChange(key, !value[key])}
          className={cn(
            "inline-flex h-6 items-center gap-1.5 rounded-4xl border px-2.5 text-xs font-medium transition-colors disabled:opacity-50",
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

/** Compact icons for the media types a query requests (renders nothing if none). */
export function MediaTypeIcons({ value }: { value: Record<MediaKey, boolean> }) {
  return (
    <>
      {MEDIA_TYPES.filter(({ key }) => value[key]).map(({ key, label, Icon }) => (
        <span key={key} title={label} className="text-muted-foreground inline-flex items-center">
          <Icon className="size-3.5" aria-label={label} />
        </span>
      ))}
    </>
  );
}
