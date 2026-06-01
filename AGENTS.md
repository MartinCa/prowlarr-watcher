# Agent Guidelines

## Project overview

Flask web app that polls Prowlarr for new search results on a cron schedule and sends notifications via Apprise. Single-file backend (`app.py`), Jinja2 templates, SQLite database, runs in Docker.

## Stack

- **Backend**: Python 3.14, Flask, SQLite (WAL mode), gunicorn (1 worker, 4 threads)
- **Scheduler**: background thread using `croniter`, wakes every 30s
- **Notifications**: Apprise
- **Frontend**: server-rendered Jinja2, htmx for the search preview, cronstrue for cron descriptions
- **Container**: Docker / Podman, data persisted in `/data`

## Key files

| File | Purpose |
|------|---------|
| `app.py` | Entire backend — DB, scheduler, routes, template filters |
| `templates/base.html` | Shared layout, all CSS, cronstrue + htmx CDN scripts |
| `templates/index.html` | Query list + add-query modal |
| `templates/query_detail.html` | Per-query results and schedule override |
| `templates/settings.html` | Prowlarr + Apprise + cron config |
| `templates/_results_fragment.html` | htmx partial for search preview |
| `requirements.txt` | Pinned runtime dependencies |
| `requirements-dev.txt` | Pinned dev tools (ruff) |

## Linting and formatting

```bash
pip install -r requirements-dev.txt
ruff check .        # lint
ruff format .       # format
ruff format --check .  # format check only (used in CI)
```

All rules are configured in `pyproject.toml`. CI runs both checks on every push and PR.

## Running locally

```bash
docker compose up --build
```

The app is available at `http://localhost:5000`. Data is persisted in `./data/`.

## Conventions

- No authentication — intentional, designed for trusted private networks only.
- All settings (Prowlarr URL, API key, Apprise URLs, default cron) are stored in the SQLite `settings` table, not environment variables.
- The scheduler runs in a daemon thread inside the gunicorn worker. Only 1 gunicorn worker is used to avoid multiple scheduler instances.
- New results are detected by hashing the `guid` (or `title|size` as fallback). Results are seeded silently on first add.
- `init_db()` and `scheduler.start()` run at module level so gunicorn picks them up on import.
