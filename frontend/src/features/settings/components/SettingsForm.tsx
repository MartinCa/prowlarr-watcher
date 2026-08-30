import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { IndexerChecklist } from "@/features/queries/components/IndexerChecklist";
import { useSaveSettings, useTestApprise, useTestProwlarr } from "@/features/settings/hooks";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/lib/api";
import { describeCron } from "@/lib/format";
import type { Settings } from "@/lib/types";

export function SettingsForm({ settings }: { settings: Settings }) {
  const [form, setForm] = useState<Settings>(settings);
  const saveSettings = useSaveSettings();
  const testProwlarr = useTestProwlarr();
  const testApprise = useTestApprise();

  function set<K extends keyof Settings>(key: K, value: Settings[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    saveSettings.mutate(form, {
      onSuccess: () => toast.success("Settings saved"),
      onError: (error) =>
        toast.error(error instanceof ApiError ? error.message : "Failed to save settings"),
    });
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle>Prowlarr</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label htmlFor="prowlarr-url">Prowlarr URL</Label>
            <Input
              id="prowlarr-url"
              value={form.prowlarrUrl}
              onChange={(e) => set("prowlarrUrl", e.target.value)}
              placeholder="http://prowlarr:9696"
            />
            <p className="text-muted-foreground text-xs">Include the port. No trailing slash.</p>
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="prowlarr-key">API Key</Label>
            <Input
              id="prowlarr-key"
              value={form.prowlarrApiKey}
              onChange={(e) => set("prowlarrApiKey", e.target.value)}
              placeholder="your api key"
            />
            <p className="text-muted-foreground text-xs">
              Found in Prowlarr → Settings → General → Security.
            </p>
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="prowlarr-external-url">External URL (optional)</Label>
            <Input
              id="prowlarr-external-url"
              value={form.prowlarrExternalUrl}
              onChange={(e) => set("prowlarrExternalUrl", e.target.value)}
              placeholder="https://prowlarr.example.com"
            />
            <p className="text-muted-foreground text-xs">
              Public URL used for links in notifications and the query view. Leave blank to use the
              Prowlarr URL above.
            </p>
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="prowlarr-timeout">Request Timeout (seconds)</Label>
            <Input
              id="prowlarr-timeout"
              type="number"
              className="max-w-32"
              value={form.prowlarrTimeout}
              onChange={(e) => set("prowlarrTimeout", Number(e.target.value))}
            />
          </div>
          <div className="flex items-center gap-3">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={testProwlarr.isPending}
              onClick={() =>
                testProwlarr.mutate({
                  prowlarrUrl: form.prowlarrUrl,
                  prowlarrApiKey: form.prowlarrApiKey,
                })
              }
            >
              Test connection
            </Button>
            {testProwlarr.data && (
              <span
                className={`font-mono text-xs ${testProwlarr.data.ok ? "text-green-600" : "text-destructive"}`}
              >
                {testProwlarr.data.message}
              </span>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Scheduling</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label htmlFor="default-cron">Default Cron Schedule</Label>
            <Input
              id="default-cron"
              className="max-w-56 font-mono"
              value={form.defaultCron}
              onChange={(e) => set("defaultCron", e.target.value)}
              placeholder="0 * * * *"
            />
            <p className="text-sm">{describeCron(form.defaultCron)}</p>
            <p className="text-muted-foreground text-xs">
              Applied to queries that don&apos;t have their own schedule.
            </p>
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="min-query-interval">Minimum Query Interval (seconds)</Label>
            <Input
              id="min-query-interval"
              type="number"
              className="max-w-32"
              value={form.minQueryInterval}
              onChange={(e) => set("minQueryInterval", Number(e.target.value))}
            />
            <p className="text-muted-foreground text-xs">
              Minimum seconds between Prowlarr API requests. Interactive requests (preview, seed)
              are prioritised over scheduled queries.
            </p>
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="max-retries">Max Retries per Query</Label>
            <Input
              id="max-retries"
              type="number"
              className="max-w-32"
              value={form.maxRetries}
              onChange={(e) => set("maxRetries", Number(e.target.value))}
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Indexers</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          <Label>Excluded by default</Label>
          <IndexerChecklist
            excluded={form.defaultExcludedIndexers}
            onChange={(excluded) => set("defaultExcludedIndexers", excluded)}
          />
          <p className="text-muted-foreground text-xs">
            Indexers checked here are skipped for every query, unless a query overrides this list.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Notifications</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label htmlFor="apprise-urls">Apprise URLs</Label>
            <Textarea
              id="apprise-urls"
              rows={5}
              value={form.appriseUrls}
              onChange={(e) => set("appriseUrls", e.target.value)}
              placeholder="gotify://hostname/token&#10;tgram://bot_token/chat_id"
            />
            <p className="text-muted-foreground text-xs">
              One URL per line. See the{" "}
              <a
                href="https://github.com/caronc/apprise/wiki"
                target="_blank"
                rel="noopener"
                className="underline"
              >
                Apprise wiki
              </a>{" "}
              for all supported services.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={testApprise.isPending}
              onClick={() => testApprise.mutate({ appriseUrls: form.appriseUrls })}
            >
              Send test notification
            </Button>
            {testApprise.data && (
              <span
                className={`font-mono text-xs ${testApprise.data.ok ? "text-green-600" : "text-destructive"}`}
              >
                {testApprise.data.message}
              </span>
            )}
          </div>
        </CardContent>
      </Card>

      <div className="flex gap-2">
        <Button type="submit" disabled={saveSettings.isPending}>
          Save Settings
        </Button>
      </div>
    </form>
  );
}
