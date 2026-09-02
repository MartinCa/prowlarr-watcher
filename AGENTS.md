# Agent Guidelines

This is the single, canonical source of agent instructions for this repository — edit guidance here, not in any tool-specific file. `CLAUDE.md` just imports this file (via `@AGENTS.md`) so Claude Code reads the same content; every other agent reads `AGENTS.md` directly.

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

# or outside Docker
DATA_DIR=./data python app.py
```

The app is available at `http://localhost:5000`. Data is persisted in `./data/`.

## Conventions

- No authentication — intentional, designed for trusted private networks or behind reverse proxy auth.
- Double-submit CSRF cookie (`csrf_token` cookie + `X-CSRF-Token` header) enforced for mutating API requests.
- All settings are stored in SQLite `settings` table, not environment variables.
- Untrusted URLs from indexers (`infoUrl`, `downloadUrl`) must be sanitized (`http:`, `https:`, `magnet:` only) to prevent stored XSS.
- The scheduler runs in a daemon thread inside the gunicorn worker. Only 1 gunicorn worker is used to avoid multiple scheduler instances.
- New results are detected by hashing the `guid` (or `title|size` as fallback). Results are seeded silently on first add.

## Architecture

**Key symbols (per module):**

- **`app.py`** — Flask app creation, Blueprint registration, startup (init_db, work_queue, scheduler). Entry point for gunicorn (`app:app`) and `python app.py`.
- **`db.py`** — SQLite setup, `get_db()`, `init_db()`, `get_setting()`/`set_setting()`, `_db_lock`.
- **`prowlarr.py`** — Prowlarr API search (`prowlarr_search_raw`), `hash_result()`, `format_size()`.
- **`worker.py`** — `Priority`, `Job`, `WorkQueue` class, `work_queue` singleton.
- **`scheduler.py`** — `Scheduler` class, `scheduler` singleton.
- **`callbacks.py`** — `process_query_result()`, `process_seed_result()`, `_insert_result()`.
- **`notifications.py`** — `notify_new_results()`, `notify_error()` via Apprise.
- **`routes.py`** — Flask Blueprint (`bp`) with all HTTP routes and template filters.

**Key subsystems:**

- **`WorkQueue`** (`worker.py`) — single worker thread draining a `PriorityQueue`. All Prowlarr searches go through `work_queue.submit()` which returns a `Job` immediately (non-blocking). The worker executes one search at a time with a configurable min gap (`min_query_interval` setting). Jobs have `Priority.HIGH` (interactive: preview, seed, run-now) or `Priority.LOW` (scheduled). Completed jobs are stored in memory with a 5-minute TTL for polling. Each job can have a `callback` invoked by the worker after the search.
- **`Scheduler`** (`scheduler.py`) — daemon thread, wakes every 30s (or when poked). Iterates enabled queries, submits due ones to the work queue. Advances `next_run` immediately on enqueue to prevent double-submission.
- **Result callbacks** (`callbacks.py`) — `process_query_result()` (for scheduled/run-now: diffs results, stores new ones, sends notifications) and `process_seed_result()` (for new query seeding: inserts all results as not-new). Both run on the worker thread.
- **Settings** (`db.py`) — key/value pairs in `settings` table. `get_setting()`/`set_setting()` hit SQLite directly (no caching).
- **`_db_lock`** (`db.py`) — global `threading.Lock` for serializing DB writes. Reads don't acquire it.

**Threading model (gunicorn: 1 worker, 4 threads):**
- Flask request threads (up to 4) — serve HTTP only, never block on Prowlarr
- 1 scheduler daemon thread — enqueues due queries, never executes searches
- 1 work-queue daemon thread — sole executor of all Prowlarr API calls

**Preview flow:** POST `/api/search-preview` submits a job and returns an HTMX polling div. GET `/api/job/<id>/preview` returns status text while queued/running, then swaps in final results (stopping the poll via outerHTML replacement without `hx-trigger`).

**Templates** (`templates/`): Jinja2 + HTMX. `base.html` has all CSS (dark theme). `_results_fragment.html` is the HTMX partial for search preview. `/api/queue-status` is polled by JS for live Queued/Running badges on query cards.

## Ruff config

`pyproject.toml`: line-length 100, target Python 3.14, lint rules E/F/W/I.

**Note:** ruff with `target-version = "py314"` formats `except (ExcA, ExcB):` to the bare `except ExcA, ExcB:` form. Under Python 3.14 that is **valid** (PEP 758 allows unparenthesized multi-except), so leave it as-is — do not "fix" it to `except Exception:`, which widens the catch. It only fails to parse on Python ≤3.13, which is why you must run tools under 3.14 (see "Python version" above).

## Before finishing any change

Run the checks in "Verification and testing" above before considering a change complete — CI enforces them on PRs to main, and catching failures locally is faster than waiting on CI. Fix any failures (or run `ruff format .` to auto-fix formatting) before committing.