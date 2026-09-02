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
      <Badge variant="outline" className="border-status-warn/40 bg-status-warn/10 text-status-warn">
        Queued
      </Badge>
    );
  }
  if (state === "running") {
    return (
      <Badge
        variant="outline"
        className="border-status-unknown/40 bg-status-unknown/10 text-status-unknown"
      >
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
