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
| `app.py` | Flask app setup, static SPA shell serving, startup (init_db, work_queue, scheduler). Entry point for gunicorn (`app:app`) and `python app.py` |
| `db.py` | SQLite setup, `get_db()`, `init_db()`, `get_setting()`/`set_setting()`, `_db_lock` |
| `routes.py` | Flask Blueprint (`bp`) with all HTTP routes under `/api/*` and double-submit-cookie CSRF validation |
| `worker.py` | `Priority`, `Job`, `WorkQueue`, `work_queue` singleton — sole executor of all Prowlarr searches |
| `scheduler.py` | `Scheduler`, `scheduler` singleton — daemon thread enqueuing due queries |
| `callbacks.py` | `process_query_result()`, `process_seed_result()`, `_insert_result()` |
| `notifications.py` | `notify_new_results()`, `notify_error()` via Apprise |
| `prowlarr.py` | Prowlarr API search (`prowlarr_search_raw`), `hash_result()`, `format_size()` |
| `frontend/` | React SPA source (routes, components, hooks, Tailwind styling) — see `frontend/AGENTS.md` and `frontend/DESIGN.md` for frontend conventions |
| `requirements.txt` / `requirements-dev.txt` | Pinned runtime dependencies / dev tools (ruff, pytest) |

## Verification and testing

### Python version — match the project, not the sandbox

The backend runs on **Python 3.14** (Dockerfile `python:3.14-slim`, CI `python-version: "3.14"`, `pyproject.toml` `target-version = "py314"`). **Running tests or parsing code with the wrong version produces false results** — e.g. 3.13 rejects the bare multi-except `except ValueError, TypeError:` that is legal in 3.14 (PEP 758), which can be misread as a syntax error in the codebase. Before running `pytest`, `python -m ast`, `compile()`, or any syntax/correctness check:

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

## Git hooks

Local hooks are installed automatically by `pnpm install` (the `prepare` script runs `lefthook install` — idempotent, safe to re-run).

Hooks come from the shared `MartinCa/lefthook-configs` fragments pinned at `v1.0.1` in `lefthook.yml` (a thin `remotes:` config). `remotes:` configs merge *over* `lefthook.yml`, so this repo's adaptations live in `lefthook-local.yml` (the one layer that overrides remotes): it adds `root: "frontend/"` to the shared TS lint/format commands so they run inside `frontend/` (inheriting the fragment's globs and `stage_fixed` handling), and it defines the Python commands (`lint-python`/`format-python`) directly — `langs/python.yml` cannot be consumed alongside `langs/ts.yml` because both define `pre-commit` commands named `lint`/`format`, and lefthook merges same-named commands key-by-key with the last one winning, so the earlier language's hooks would be silently destroyed. The local Python commands use exactly the fragment's commands (`uvx ruff check --fix` / `uvx ruff format` over the staged `*.{py,pyi}`), run from the repo root, and respect `pyproject.toml`'s `[tool.ruff]` (including `target-version = "py314"`, so the PEP 758 bare multi-`except` forms parse correctly under any sandbox Python).

- **pre-commit** — TS lint/format via ESLint `--fix` + Prettier `--write` on staged TS/TSX and Prettier on JSON/CSS/MD/JS/MJS/HTML, run from `frontend/` and re-staging fixed files; Python lint/format via `uvx ruff check --fix` + `uvx ruff format` on staged `*.py`/`*.pyi`, re-staging fixed files. `lefthook-shared.yml` secret-scans the staged diff with `betterleaks` (blocks the commit on a leak) and audits staged `.github/workflows/*` files with `zizmor` (blocks on a finding).
- **commit-msg** — `commit-msg.yml` enforces Conventional Commits, e.g. `feat: ...`, `fix(api): ...`.

Lint/format are enforced both locally (these hooks) and in CI (the `lint` job runs `ruff check .` + `ruff format --check .` and `pnpm run lint` + `pnpm run format-check` + `tsc --noEmit` inside `frontend/`). The secret scan and Conventional-Commits validation are hook-only: CI runs pytest/frontend tests and uploads a zizmor SARIF report to code scanning — it does not run `betterleaks` or validate commit messages itself, and the zizmor CI job is a non-blocking SARIF report, not a merge gate. Do not bypass the hooks.

Two hook tools must be on `PATH`: `betterleaks` (secret scan, install per its project README) and `zizmor` (workflow audit, install from zizmor.sh). If a tool is missing, `LEFTHOOK=0 git commit` skips the hooks entirely — a pragmatic escape hatch for restricted setups, not a way to dodge the gates.

`lefthook-local.yml` is **intentionally checked in** as this repo's team-wide override: in a stock lefthook setup that file is the personal, gitignored override layer, but here it is the one layer that merges *over* the shared `remotes:` fragments, and it carries the repo-wide `frontend/` root override plus the Python command adaptation (the `langs/python.yml` collision noted above). It is not a personal override layer in this repo; do not use it for private changes.

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

**Preview flow:** POST `/api/search-preview` submits a job and returns `{"jobId": ...}` (202). The frontend polls GET `/api/jobs/<job_id>` — status while queued/running, results (or error) once done. GET `/api/queue-status` returns live per-query Queued/Running state (plus preview state) for the badges on query cards.

## Ruff config

`pyproject.toml`: line-length 100, target Python 3.14, lint rules E/F/W/I.

**Note:** ruff with `target-version = "py314"` formats `except (ExcA, ExcB):` to the bare `except ExcA, ExcB:` form. Under Python 3.14 that is **valid** (PEP 758 allows unparenthesized multi-except), so leave it as-is — do not "fix" it to `except Exception:`, which widens the catch. It only fails to parse on Python ≤3.13, which is why you must run tools under 3.14 (see "Python version" above).

## Before finishing any change

Run the checks in "Verification and testing" above before considering a change complete — CI enforces them on PRs to main, and catching failures locally is faster than waiting on CI. Fix any failures (or run `ruff format .` to auto-fix formatting) before committing.
