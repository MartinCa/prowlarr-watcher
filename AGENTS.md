# Agent Guidelines

## Project overview

Flask web app that polls Prowlarr for new search results on a cron schedule and sends notifications via Apprise. Modular Python backend with SQLite persistence, single-page React frontend, runs in Docker.

## Stack

- **Backend**: Python 3.14, Flask, SQLite (WAL mode), gunicorn (1 worker, 4 threads)
- **Scheduler**: background thread using `croniter`, wakes every 30s
- **Work Queue**: single worker thread draining a `PriorityQueue` for rate-limited Prowlarr requests
- **Notifications**: Apprise
- **Frontend**: React 19, Vite, TanStack Router, TanStack Query, Tailwind CSS, shadcn/ui
- **Container**: Docker / Podman, data persisted in `/data`

## Key files

| File | Purpose |
|------|---------|
| `app.py` | Flask app setup, static SPA shell serving, startup initialization |
| `db.py` | SQLite connection helpers, schema setup, settings get/set, write lock |
| `routes.py` | REST API routes under `/api/*` with double-submit cookie CSRF validation |
| `worker.py` | Background `WorkQueue` executor for all Prowlarr searches |
| `scheduler.py` | Background `Scheduler` daemon thread enqueuing due queries |
| `callbacks.py` | Post-search callbacks for seeding and diffing/inserting query results |
| `notifications.py` | Apprise notification delivery for new results and query errors |
| `prowlarr.py` | Prowlarr API client, result hashing, and formatting |
| `frontend/` | React SPA source (routes, components, hooks, Tailwind styling) |
| `requirements.txt` | Pinned runtime dependencies |
| `requirements-dev.txt` | Pinned dev tools (ruff, pytest) |

## Verification and testing

### Backend
```bash
pip install -r requirements-dev.txt
ruff check .           # lint
ruff format --check .  # format check
pytest test_app.py -v  # run test suite
```

### Frontend
```bash
cd frontend
pnpm lint              # eslint
pnpm build             # vite build && tsc -b
```

## Running locally

```bash
docker compose up --build
```

The app is available at `http://localhost:5000`. Data is persisted in `./data/`.

## Conventions

- No authentication — intentional, designed for trusted private networks or behind reverse proxy auth.
- Double-submit CSRF cookie (`csrf_token` cookie + `X-CSRF-Token` header) enforced for mutating API requests.
- All settings are stored in SQLite `settings` table, not environment variables.
- Untrusted URLs from indexers (`infoUrl`, `downloadUrl`) must be sanitized (`http:`, `https:`, `magnet:` only) to prevent stored XSS.
- The scheduler runs in a daemon thread inside the gunicorn worker. Only 1 gunicorn worker is used to avoid multiple scheduler instances.
- New results are detected by hashing the `guid` (or `title|size` as fallback). Results are seeded silently on first add.

