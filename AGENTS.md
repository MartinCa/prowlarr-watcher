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

### Python version — match the project, not the sandbox

The backend runs on **Python 3.14** (Dockerfile `python:3.14-slim`, CI `python-version: "3.14"`, `pyproject.toml` `target-version = "py314"`). Sandboxed / OpenHands agents often default to an older interpreter (this sandbox ships Python 3.13). **Running tests or parsing code with the wrong version produces false results** — e.g. 3.13 rejects the bare multi-except `except ValueError, TypeError:` that is legal in 3.14 (PEP 758), which can be misread as a syntax error in the codebase.

Before running `pytest`, `python -m ast`, `compile()`, or any syntax/correctness check:

1. Use Python 3.14. If the sandbox doesn't provide it, get one: `uv python install 3.14` then `uv run --python 3.14 ...` (or `uvx --python 3.14`).
2. Don't conclude code is broken from a parse/test failure until you've re-run it under the project's Python version.

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

