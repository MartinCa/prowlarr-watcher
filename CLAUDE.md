# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-file Flask web app that periodically searches Prowlarr for new results and sends notifications via Apprise. SQLite for persistence, HTMX for interactivity, gunicorn for production serving.

## Commands

```bash
# Lint & format (CI runs these on PRs to main)
ruff check .
ruff format --check .
ruff format .          # auto-fix

# Tests
pytest test_app.py -v          # all tests
pytest test_app.py::TestWorkQueue -v   # single class
pytest test_app.py::TestWorkQueue::test_priority_ordering -v  # single test

# Run locally (outside Docker)
DATA_DIR=./data python app.py

# Docker
docker compose up -d
```

## Architecture

Everything lives in `app.py` (~700 lines). No packages, no blueprints.

**Key subsystems:**

- **`WorkQueue`** — single worker thread draining a `PriorityQueue`. All Prowlarr searches go through `work_queue.submit()` which returns a `Job` immediately (non-blocking). The worker executes one search at a time with a configurable min gap (`min_query_interval` setting). Jobs have `Priority.HIGH` (interactive: preview, seed, run-now) or `Priority.LOW` (scheduled). Completed jobs are stored in memory with a 5-minute TTL for polling. Each job can have a `callback` invoked by the worker after the search.
- **`Scheduler`** — daemon thread, wakes every 30s (or when poked). Iterates enabled queries, submits due ones to the work queue. Advances `next_run` immediately on enqueue to prevent double-submission.
- **Result callbacks** — `_process_query_result()` (for scheduled/run-now: diffs results, stores new ones, sends notifications) and `_process_seed_result()` (for new query seeding: inserts all results as not-new). Both run on the worker thread.
- **Settings** — key/value pairs in `settings` table. `get_setting()`/`set_setting()` hit SQLite directly (no caching).
- **`_db_lock`** — global `threading.Lock` for serializing DB writes. Reads don't acquire it.

**Threading model (gunicorn: 1 worker, 4 threads):**
- Flask request threads (up to 4) — serve HTTP only, never block on Prowlarr
- 1 scheduler daemon thread — enqueues due queries, never executes searches
- 1 work-queue daemon thread — sole executor of all Prowlarr API calls

**Preview flow:** POST `/api/search-preview` submits a job and returns an HTMX polling div. GET `/api/job/<id>/preview` returns status text while queued/running, then swaps in final results (stopping the poll via outerHTML replacement without `hx-trigger`).

**Templates** (`templates/`): Jinja2 + HTMX. `base.html` has all CSS (dark theme). `_results_fragment.html` is the HTMX partial for search preview. `/api/queue-status` is polled by JS for live Queued/Running badges on query cards.

## Ruff config

`pyproject.toml`: line-length 100, target Python 3.14, lint rules E/F/W/I.

**Caveat:** ruff with `target-version = "py314"` incorrectly reformats `except (ExcA, ExcB):` to `except ExcA, ExcB:` (Python 2 syntax). Use `except Exception:` as a workaround when catching multiple exception types.
