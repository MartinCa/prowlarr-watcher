import { Badge } from "@/components/ui/badge";

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
        className="border-status-error/40 bg-status-error/10 text-status-error"
      >
        Error
      </Badge>
    );
  }
  return null;
}
