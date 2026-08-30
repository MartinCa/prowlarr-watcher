import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export function QueryStatusBadge({
  state,
  hasError,
}: {
  state: "queued" | "running" | undefined;
  hasError: boolean;
}) {
  if (state === "queued") {
    return (
      <Badge variant="outline" className="border-amber-500/40 bg-amber-500/10 text-amber-600">
        Queued
      </Badge>
    );
  }
  if (state === "running") {
    return (
      <Badge variant="outline" className="border-blue-500/40 bg-blue-500/10 text-blue-600">
        Running
      </Badge>
    );
  }
  if (hasError) {
    return (
      <Badge
        variant="outline"
        className={cn("border-destructive/40 bg-destructive/10 text-destructive")}
      >
        Error
      </Badge>
    );
  }
  return null;
}
