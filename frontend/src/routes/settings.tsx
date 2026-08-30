import { createFileRoute } from "@tanstack/react-router";
import { SettingsForm } from "@/features/settings/components/SettingsForm";
import { useSettings } from "@/features/settings/hooks";

export const Route = createFileRoute("/settings")({ component: SettingsPage });

function SettingsPage() {
  const settings = useSettings();

  if (settings.isPending) {
    return <p className="text-muted-foreground text-sm">Loading…</p>;
  }
  if (settings.isError) {
    return <p className="text-destructive text-sm">{settings.error.message}</p>;
  }

  return (
    <div className="flex flex-col gap-6">
      <h1 className="font-mono text-lg font-medium">Settings</h1>
      <SettingsForm settings={settings.data} />
    </div>
  );
}
