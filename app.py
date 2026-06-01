#!/usr/bin/env python3
"""
Prowlarr Search Watcher — Flask web application
"""

import hashlib
import logging
import os
import queue
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path

import apprise
import requests
from croniter import croniter
from flask import Flask, jsonify, redirect, render_template, request, url_for

# ---------------------------------------------------------------------------
# Config & logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("prowlarr-watcher")

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "watcher.db"

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
_db_lock = threading.Lock()


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS queries (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                query       TEXT NOT NULL,
                cron        TEXT,
                enabled     INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT NOT NULL,
                last_run    TEXT,
                next_run    TEXT,
                last_count  INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS results (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                query_id    INTEGER NOT NULL REFERENCES queries(id) ON DELETE CASCADE,
                result_hash TEXT NOT NULL,
                title       TEXT,
                indexer     TEXT,
                size        INTEGER,
                guid        TEXT,
                info_url    TEXT,
                download_url TEXT,
                seeders     INTEGER,
                first_seen  TEXT NOT NULL,
                is_new      INTEGER NOT NULL DEFAULT 1,
                UNIQUE(query_id, result_hash)
            );
        """)
        # Default settings
        conn.execute(
            "INSERT OR IGNORE INTO settings VALUES ('prowlarr_url', 'http://prowlarr:9696')"
        )
        conn.execute("INSERT OR IGNORE INTO settings VALUES ('prowlarr_api_key', '')")
        conn.execute("INSERT OR IGNORE INTO settings VALUES ('default_cron', '0 * * * *')")
        conn.execute("INSERT OR IGNORE INTO settings VALUES ('apprise_urls', '')")
        conn.execute("INSERT OR IGNORE INTO settings VALUES ('min_query_interval', '10')")
        conn.commit()


def get_setting(key: str, default: str = "") -> str:
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str):
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, value))
        conn.commit()


# ---------------------------------------------------------------------------
# Prowlarr API helpers
# ---------------------------------------------------------------------------
def _prowlarr_search_raw(query: str, categories: list[int] | None = None) -> list[dict]:
    base = get_setting("prowlarr_url").rstrip("/")
    api_key = get_setting("prowlarr_api_key")
    if not base or not api_key:
        raise ValueError("Prowlarr URL and API key must be configured in Settings")

    params: dict = {"query": query}
    if categories:
        params["categories"] = categories

    resp = requests.get(
        f"{base}/api/v1/search",
        headers={"X-Api-Key": api_key},
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    results = resp.json()
    log.info("Search %r → %d results", query, len(results))
    return results


def hash_result(r: dict) -> str:
    key = r.get("guid") or f"{r.get('title', '')}|{r.get('size', '')}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def format_size(size_bytes: int | None) -> str:
    if not size_bytes:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


# ---------------------------------------------------------------------------
# Work queue — single worker thread for all Prowlarr searches
# ---------------------------------------------------------------------------
class Priority(IntEnum):
    HIGH = 0  # interactive: preview, seed, run-now
    LOW = 1  # scheduled queries


@dataclass
class Job:
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    query: str = ""
    categories: list[int] | None = None
    label: str = "unknown"
    priority: Priority = Priority.LOW
    callback: Callable[["Job"], None] | None = None
    status: str = "queued"  # queued -> running -> done | error
    result: list[dict] | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.monotonic)
    _seq: int = 0

    def __lt__(self, other: "Job") -> bool:
        return (self.priority, self._seq) < (other.priority, other._seq)


class WorkQueue:
    _JOB_TTL = 300.0  # seconds to keep completed jobs

    def __init__(self):
        self._pq: queue.PriorityQueue[Job] = queue.PriorityQueue()
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._active_labels: set[str] = set()
        self._running: Job | None = None
        self._seq = 0
        self._thread: threading.Thread | None = None

    def start(self):
        self._thread = threading.Thread(target=self._worker, daemon=True, name="work-queue")
        self._thread.start()
        log.info("Work queue started")

    def _min_gap(self) -> float:
        try:
            return max(0.0, float(get_setting("min_query_interval", "10")))
        except Exception:
            return 10.0

    def submit(
        self,
        query: str,
        categories: list[int] | None = None,
        label: str = "unknown",
        priority: Priority = Priority.LOW,
        callback: Callable[[Job], None] | None = None,
    ) -> Job:
        with self._lock:
            # Reject duplicate labels (e.g. double-click "Run Now")
            if label in self._active_labels:
                for j in self._jobs.values():
                    if j.label == label and j.status in ("queued", "running"):
                        return j

            self._seq += 1
            job = Job(
                query=query,
                categories=categories,
                label=label,
                priority=priority,
                callback=callback,
                _seq=self._seq,
            )
            self._jobs[job.job_id] = job
            self._active_labels.add(label)

        self._pq.put(job)
        self._cleanup()
        return job

    def get_job(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def status(self) -> dict:
        with self._lock:
            queued = {j.label for j in self._jobs.values() if j.status == "queued"}
            running = self._running.label if self._running else None
            return {"queued": queued, "running": running}

    def _worker(self):
        last_request: float = 0.0
        while True:
            job = self._pq.get()
            with self._lock:
                job.status = "running"
                self._running = job

            gap = self._min_gap()
            wait = gap - (time.monotonic() - last_request)
            if wait > 0:
                log.debug("Rate-limiting Prowlarr request, sleeping %.1fs", wait)
                time.sleep(wait)

            try:
                job.result = _prowlarr_search_raw(job.query, job.categories)
                job.status = "done"
            except Exception as exc:
                job.error = str(exc)
                job.status = "error"
                log.error("Search failed for %r: %s", job.label, exc)
            finally:
                last_request = time.monotonic()
                with self._lock:
                    self._running = None
                    self._active_labels.discard(job.label)

            if job.callback:
                try:
                    job.callback(job)
                except Exception:
                    log.exception("Callback failed for job %s (%s)", job.job_id, job.label)

    def _cleanup(self):
        now = time.monotonic()
        with self._lock:
            expired = [
                jid
                for jid, j in self._jobs.items()
                if j.status in ("done", "error") and (now - j.created_at) > self._JOB_TTL
            ]
            for jid in expired:
                del self._jobs[jid]


work_queue = WorkQueue()


# ---------------------------------------------------------------------------
# Result processing callbacks
# ---------------------------------------------------------------------------
def _process_query_result(qid: int, cron_expr: str, job: Job):
    now_iso = datetime.now(timezone.utc).isoformat()
    next_iso = Scheduler.compute_next(cron_expr)

    if job.status == "error":
        log.error("[Q%d] Search failed: %s", qid, job.error)
        with _db_lock, get_db() as conn:
            conn.execute(
                "UPDATE queries SET last_run=?, next_run=? WHERE id=?",
                (now_iso, next_iso, qid),
            )
            conn.commit()
        return

    raw = job.result or []

    with get_db() as conn:
        row = conn.execute("SELECT name, query FROM queries WHERE id=?", (qid,)).fetchone()
    if not row:
        return

    with _db_lock, get_db() as conn:
        seen = {
            r["result_hash"]
            for r in conn.execute(
                "SELECT result_hash FROM results WHERE query_id=?", (qid,)
            ).fetchall()
        }

        new_items = []
        for r in raw:
            h = hash_result(r)
            if h not in seen:
                new_items.append(r)
                conn.execute(
                    """INSERT OR IGNORE INTO results
                       (query_id, result_hash, title, indexer, size, guid,
                        info_url, download_url, seeders, first_seen, is_new)
                       VALUES (?,?,?,?,?,?,?,?,?,?,1)""",
                    (
                        qid,
                        h,
                        r.get("title"),
                        r.get("indexer"),
                        r.get("size"),
                        r.get("guid"),
                        r.get("infoUrl"),
                        r.get("downloadUrl"),
                        r.get("seeders"),
                        now_iso,
                    ),
                )

        conn.execute(
            "UPDATE queries SET last_run=?, next_run=?, last_count=? WHERE id=?",
            (now_iso, next_iso, len(raw), qid),
        )
        conn.commit()

    log.info("[Q%d] %d total / %d new", qid, len(raw), len(new_items))

    if new_items:
        _notify(row["name"], row["query"], new_items)


def _process_seed_result(qid: int, job: Job):
    now_iso = datetime.now(timezone.utc).isoformat()

    if job.status == "error":
        log.warning("[Q%d] Seed search failed: %s", qid, job.error)
        return

    raw = job.result or []

    with _db_lock, get_db() as conn:
        for r in raw:
            h = hash_result(r)
            conn.execute(
                """INSERT OR IGNORE INTO results
                   (query_id, result_hash, title, indexer, size, guid,
                    info_url, download_url, seeders, first_seen, is_new)
                   VALUES (?,?,?,?,?,?,?,?,?,?,0)""",
                (
                    qid,
                    h,
                    r.get("title"),
                    r.get("indexer"),
                    r.get("size"),
                    r.get("guid"),
                    r.get("infoUrl"),
                    r.get("downloadUrl"),
                    r.get("seeders"),
                    now_iso,
                ),
            )
        conn.execute(
            "UPDATE queries SET last_run=?, last_count=? WHERE id=?",
            (now_iso, len(raw), qid),
        )
        conn.commit()

    log.info("[Q%d] Seeded with %d results", qid, len(raw))


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
class Scheduler:
    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wakeup = threading.Event()

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="scheduler")
        self._thread.start()
        log.info("Scheduler started")

    def stop(self):
        self._stop.set()
        self._wakeup.set()

    def poke(self):
        """Wake the scheduler early (e.g. after config change)."""
        self._wakeup.set()

    def _loop(self):
        while not self._stop.is_set():
            self._wakeup.clear()
            self._tick()
            self._wakeup.wait(timeout=30)

    def _tick(self):
        now_ts = time.time()

        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, query, cron, next_run, enabled FROM queries WHERE enabled=1"
            ).fetchall()

        for row in rows:
            qid = row["id"]
            cron_expr = row["cron"] or get_setting("default_cron", "0 * * * *")

            next_run_iso = row["next_run"]
            if not next_run_iso:
                next_run_iso = self.compute_next(cron_expr)
                with _db_lock, get_db() as conn:
                    conn.execute("UPDATE queries SET next_run=? WHERE id=?", (next_run_iso, qid))
                    conn.commit()

            next_run_ts = datetime.fromisoformat(next_run_iso).timestamp()
            if now_ts >= next_run_ts:
                # Advance next_run immediately to prevent re-enqueue
                next_iso = self.compute_next(cron_expr)
                with _db_lock, get_db() as conn:
                    conn.execute("UPDATE queries SET next_run=? WHERE id=?", (next_iso, qid))
                    conn.commit()

                work_queue.submit(
                    query=row["query"],
                    label=f"q:{qid}",
                    priority=Priority.LOW,
                    callback=lambda job, _qid=qid, _cron=cron_expr: _process_query_result(
                        _qid, _cron, job
                    ),
                )

    @staticmethod
    def compute_next(cron_expr: str) -> str:
        cit = croniter(cron_expr, datetime.now(timezone.utc))
        return cit.get_next(datetime).isoformat()


scheduler = Scheduler()

# Initialize DB and start background threads when the module is loaded
# (works with both `python app.py` and gunicorn importing the module).
init_db()
work_queue.start()
scheduler.start()


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------
def _notify(name: str, query: str, new_items: list[dict]):
    raw_urls = get_setting("apprise_urls", "")
    urls = [u.strip() for u in raw_urls.splitlines() if u.strip()]
    if not urls:
        log.info("No Apprise URLs configured, skipping notification")
        return

    count = len(new_items)
    title = f"[Prowlarr] {count} new result{'s' if count != 1 else ''} — {name}"
    lines = []
    for r in new_items[:20]:
        size_str = format_size(r.get("size"))
        lines.append(f"• {r.get('title', '?')}  [{r.get('indexer', '?')}] [{size_str}]")
    if count > 20:
        lines.append(f"  … and {count - 20} more")
    body = "\n".join(lines)

    ap = apprise.Apprise()
    for u in urls:
        ap.add(u)
    ap.notify(title=title, body=body)
    log.info("Notification sent: %s", title)


# ---------------------------------------------------------------------------
# Routes — pages
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    with get_db() as conn:
        queries = conn.execute("SELECT * FROM queries ORDER BY id DESC").fetchall()
        default_cron = get_setting("default_cron", "0 * * * *")
    return render_template("index.html", queries=queries, default_cron=default_cron)


@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        set_setting("prowlarr_url", request.form.get("prowlarr_url", "").strip())
        set_setting("prowlarr_api_key", request.form.get("prowlarr_api_key", "").strip())
        set_setting("default_cron", request.form.get("default_cron", "0 * * * *").strip())
        set_setting(
            "min_query_interval",
            request.form.get("min_query_interval", "10").strip(),
        )
        set_setting("apprise_urls", request.form.get("apprise_urls", "").strip())
        scheduler.poke()
        return redirect(url_for("settings") + "?saved=1")

    return render_template(
        "settings.html",
        prowlarr_url=get_setting("prowlarr_url"),
        prowlarr_api_key=get_setting("prowlarr_api_key"),
        default_cron=get_setting("default_cron"),
        min_query_interval=get_setting("min_query_interval", "10"),
        apprise_urls=get_setting("apprise_urls"),
        saved=request.args.get("saved"),
    )


@app.route("/query/<int:qid>")
def query_detail(qid: int):
    with get_db() as conn:
        q = conn.execute("SELECT * FROM queries WHERE id=?", (qid,)).fetchone()
        if not q:
            return "Not found", 404
        results = conn.execute(
            "SELECT * FROM results WHERE query_id=? ORDER BY first_seen DESC", (qid,)
        ).fetchall()
    default_cron = get_setting("default_cron", "0 * * * *")
    return render_template(
        "query_detail.html",
        q=q,
        results=results,
        default_cron=default_cron,
        format_size=format_size,
    )


# ---------------------------------------------------------------------------
# Routes — API / htmx actions
# ---------------------------------------------------------------------------
@app.route("/api/search-preview", methods=["POST"])
def search_preview():
    """Submit a preview search and return a polling fragment."""
    query_text = request.form.get("query", "").strip()
    if not query_text:
        return "<p class='preview-empty'>Enter a query above to preview results.</p>"
    job = work_queue.submit(query_text, label="preview", priority=Priority.HIGH)
    return (
        f'<div hx-get="/api/job/{job.job_id}/preview"'
        f' hx-trigger="load, every 1s"'
        f' hx-swap="outerHTML">'
        f'<span style="font-family:var(--mono);font-size:12px;color:var(--muted)">'
        f"searching Prowlarr…</span></div>"
    )


@app.route("/api/job/<job_id>/preview")
def job_preview(job_id: str):
    """Poll endpoint for preview results."""
    job = work_queue.get_job(job_id)
    if not job:
        return "<p class='preview-error'>Job expired or not found.</p>"
    if job.status in ("queued", "running"):
        status_text = (
            "queued — waiting for other searches…"
            if job.status == "queued"
            else "searching Prowlarr…"
        )
        return (
            f'<div hx-get="/api/job/{job_id}/preview"'
            f' hx-trigger="every 1s"'
            f' hx-swap="outerHTML">'
            f'<span style="font-family:var(--mono);font-size:12px;color:'
            f'{"var(--yellow)" if job.status == "queued" else "var(--muted)"}">'
            f"{status_text}</span></div>"
        )
    if job.status == "error":
        return f"<p class='preview-error'>Search failed: {job.error}</p>"
    return render_template(
        "_results_fragment.html", results=job.result, format_size=format_size, is_preview=True
    )


@app.route("/api/query", methods=["POST"])
def add_query():
    name = request.form.get("name", "").strip()
    query_text = request.form.get("query", "").strip()
    cron = request.form.get("cron", "").strip() or None

    if not name or not query_text:
        return "Name and query are required", 400

    now_iso = datetime.now(timezone.utc).isoformat()
    cron_expr = cron or get_setting("default_cron", "0 * * * *")
    next_iso = Scheduler.compute_next(cron_expr)

    with _db_lock, get_db() as conn:
        cur = conn.execute(
            "INSERT INTO queries (name, query, cron, created_at, next_run) VALUES (?,?,?,?,?)",
            (name, query_text, cron, now_iso, next_iso),
        )
        qid = cur.lastrowid
        conn.commit()

    work_queue.submit(
        query=query_text,
        label="seed",
        priority=Priority.HIGH,
        callback=lambda job, _qid=qid: _process_seed_result(_qid, job),
    )
    scheduler.poke()
    return redirect(url_for("index"))


@app.route("/api/query/<int:qid>", methods=["POST"])
def update_query(qid: int):
    action = request.form.get("action")

    if action == "delete":
        with _db_lock, get_db() as conn:
            conn.execute("DELETE FROM queries WHERE id=?", (qid,))
            conn.commit()
        scheduler.poke()
        return redirect(url_for("index"))

    if action == "toggle":
        with _db_lock, get_db() as conn:
            row = conn.execute("SELECT enabled FROM queries WHERE id=?", (qid,)).fetchone()
            new_val = 0 if row["enabled"] else 1
            conn.execute("UPDATE queries SET enabled=? WHERE id=?", (new_val, qid))
            conn.commit()
        scheduler.poke()
        return redirect(url_for("index"))

    if action == "run_now":
        with get_db() as conn:
            row = conn.execute("SELECT query, cron FROM queries WHERE id=?", (qid,)).fetchone()
        if row:
            cron_expr = row["cron"] or get_setting("default_cron", "0 * * * *")
            work_queue.submit(
                query=row["query"],
                label=f"q:{qid}",
                priority=Priority.HIGH,
                callback=lambda job, _qid=qid, _cron=cron_expr: _process_query_result(
                    _qid, _cron, job
                ),
            )
        return redirect(url_for("query_detail", qid=qid))

    if action == "update_cron":
        cron = request.form.get("cron", "").strip() or None
        next_iso = Scheduler.compute_next(cron or get_setting("default_cron", "0 * * * *"))
        with _db_lock, get_db() as conn:
            conn.execute(
                "UPDATE queries SET cron=?, next_run=? WHERE id=?",
                (cron, next_iso, qid),
            )
            conn.commit()
        scheduler.poke()
        return redirect(url_for("query_detail", qid=qid))

    return "Unknown action", 400


@app.route("/api/queue-status")
def queue_status():
    """Return the current work queue state for UI polling."""
    st = work_queue.status()
    query_states: dict[str, str] = {}
    for label in st["queued"]:
        if label.startswith("q:"):
            query_states[label[2:]] = "queued"
    if st["running"] and st["running"].startswith("q:"):
        query_states[st["running"][2:]] = "running"
    preview_state = None
    if "preview" in st["queued"]:
        preview_state = "queued"
    elif st["running"] == "preview":
        preview_state = "running"
    return jsonify({"queries": query_states, "preview": preview_state})


@app.route("/api/test-prowlarr", methods=["POST"])
def test_prowlarr():
    base = request.form.get("prowlarr_url", "").strip().rstrip("/")
    api_key = request.form.get("prowlarr_api_key", "").strip()
    if not base or not api_key:
        return jsonify({"ok": False, "message": "URL and API key are required"})
    try:
        resp = requests.get(
            f"{base}/api/v1/system/status",
            headers={"X-Api-Key": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        version = data.get("version", "unknown")
        return jsonify({"ok": True, "message": f"Connected — Prowlarr v{version}"})
    except requests.exceptions.ConnectionError:
        return jsonify({"ok": False, "message": "Connection refused — check the URL"})
    except requests.exceptions.Timeout:
        return jsonify({"ok": False, "message": "Request timed out"})
    except requests.exceptions.HTTPError as exc:
        code = exc.response.status_code
        msg = "Unauthorized — check the API key" if code == 401 else f"HTTP {code}"
        return jsonify({"ok": False, "message": msg})
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)})


@app.route("/api/test-apprise", methods=["POST"])
def test_apprise():
    raw_urls = request.form.get("apprise_urls", "")
    urls = [u.strip() for u in raw_urls.splitlines() if u.strip()]
    if not urls:
        return jsonify({"ok": False, "message": "No Apprise URLs configured"})
    ap = apprise.Apprise()
    for u in urls:
        ap.add(u)
    ok = ap.notify(title="Prowlarr Watcher — test", body="Notification delivery confirmed ✓")
    msg = "Sent!" if ok else "Delivery may have failed — check your URLs"
    return jsonify({"ok": ok, "message": msg})


# ---------------------------------------------------------------------------
# Template filters
# ---------------------------------------------------------------------------
@app.template_filter("timeago")
def timeago_filter(iso: str | None) -> str:
    if not iso:
        return "never"
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        diff = datetime.now(timezone.utc) - dt
        secs = int(diff.total_seconds())
        if secs < 0:
            secs = -secs
            if secs < 60:
                return "in <1m"
            if secs < 3600:
                return f"in {secs // 60}m"
            if secs < 86400:
                return f"in {secs // 3600}h"
            return f"in {secs // 86400}d"
        if secs < 60:
            return "just now"
        if secs < 3600:
            return f"{secs // 60}m ago"
        if secs < 86400:
            return f"{secs // 3600}h ago"
        return f"{secs // 86400}d ago"
    except Exception:
        return iso


@app.template_filter("fmt_size")
def fmt_size_filter(size: int | None) -> str:
    return format_size(size)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
