# Prowlarr Watcher

A small web app that watches [Prowlarr](https://github.com/Prowlarr/Prowlarr) for new search results and sends notifications when they appear.

You define named search queries with a cron schedule. On each run the app diffs results against what it has already seen and notifies you — via any service supported by [Apprise](https://github.com/caronc/apprise) — only when genuinely new results show up.

## Security

The web UI has no authentication by design — it is intended to run on a trusted private network or behind a reverse proxy that handles access control. Do not expose it directly to the internet. The settings page displays your Prowlarr API key and Apprise notification URLs in plaintext.

## Requirements

- Docker and Docker Compose
- A running Prowlarr instance with at least one indexer configured

## Running

```bash
docker compose up -d
```

The web UI is available at `http://localhost:5000`.

Data (SQLite database) is persisted in `./data/`.

### Connecting to Prowlarr on the same Docker network

If Prowlarr runs in Docker too, uncomment the `networks` block in `docker-compose.yml` and set the Prowlarr URL to `http://prowlarr:9696` (or whatever the container name is).

## First-time setup

1. Open **Settings** and enter your Prowlarr URL and API key.
   - API key: Prowlarr → Settings → General → Security
   - Use **Test connection** to verify before saving.
2. Optionally configure notification URLs (one Apprise URL per line) and use **Send test notification** to confirm delivery.
3. Set a default cron schedule (default: `0 * * * *` — every hour).
4. Go to **Queries** and add your first search.

## Adding a query

Click **+ Add Query**, give it a name, type a search term and hit **Preview** to see live results from Prowlarr before saving. The current results are seeded silently on first add — you will only be notified about results that appear *after* that point.

Each query can have its own cron schedule; leave it blank to use the default.

## Notifications

Any [Apprise-supported service](https://github.com/caronc/apprise/wiki) works — Gotify, ntfy, Telegram, Discord, Slack, email, Pushover, and many more. Add one URL per line in Settings.

Example URLs:

```
gotify://hostname/token
ntfys://ntfy.sh/your-topic
tgram://bot_token/chat_id
discord://webhook_id/webhook_token
```

## Development

```bash
pip install -r requirements-dev.txt
```

Lint and format checks:

```bash
ruff check .
ruff format --check .
```

To auto-fix formatting:

```bash
ruff format .
```

## Environment variables

| Variable   | Default  | Description                        |
|------------|----------|------------------------------------|
| `DATA_DIR` | `/data`  | Directory for the SQLite database  |
| `TZ`       | `Europe/London` | Timezone for cron scheduling — set to your local timezone |
