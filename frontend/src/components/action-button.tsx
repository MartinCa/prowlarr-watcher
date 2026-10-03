import type { ComponentProps, ElementType } from "react";
import { Loader2Icon } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { ActionStatus } from "@/hooks/use-async-action";
import { cn } from "@/lib/utils";

type ActionButtonProps = Omit<ComponentProps<typeof Button>, "children"> & {
  /** The button's resting icon (a lucide icon component). It stays visible after the action, tinted by the result. */
  icon: ElementType<{ className?: string; "aria-hidden"?: boolean }>;
  /** What the button does, as a verb phrase ("Send to download client"). Becomes the accessible name and tooltip. */
  label: string;
  status?: ActionStatus;
  /** Replaces `label` in the accessible name and tooltip after a result, e.g. "Sent to qBittorrent". */
  resultLabel?: string;
};

const statusTint: Record<ActionStatus, string | undefined> = {
  idle: undefined,
  pending: undefined,
  success: "text-status-ok",
  error: "text-status-error",
};

/**
 * Icon button for an action whose outcome matters: idle shows the icon, pending swaps in a
 * spinner (same size, no layout shift), and a result tints the icon green or red until the next
 * click or until the button unmounts. Put the result *text* in a toast (`notifications`), not
 * next to the button. Colour is not the only signal: `resultLabel` updates the accessible name
 * and tooltip, and `data-status` is exposed for styling and tests.
 */
export function ActionButton({
  icon: Icon,
  label,
  status = "idle",
  resultLabel,
  disabled,
  className,
  variant = "outline",
  size = "icon-sm",
  ...props
}: ActionButtonProps) {
  const pending = status === "pending";
  const name = (status === "success" || status === "error") && resultLabel ? resultLabel : label;

  return (
    <Button
      variant={variant}
      size={size}
      aria-label={name}
      title={name}
      aria-busy={pending}
      data-status={status}
      disabled={disabled || pending}
      className={cn(className)}
      {...props}
    >
      {pending ? (
        <Loader2Icon className="animate-spin" aria-hidden />
      ) : (
        <Icon className={statusTint[status]} aria-hidden />
      )}
    </Button>
  );
}
