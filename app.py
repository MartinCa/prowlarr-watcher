#!/usr/bin/env python3
"""
Prowlarr Search Watcher — Flask web application
"""

import hashlib
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
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


class _ProwlarrLimiter:
    """Priority queue ensuring one Prowlarr search at a time with a configurable gap."""

    PRIORITY_HIGH = 0  # interactive (preview, seed)
    PRIORITY_LOW = 1  # scheduled queries

    def __init__(self):
        self._cond = threading.Condition(threading.Lock())
        self._last_request: float = 0.0
        self._seq = 0
        self._waiters: dict[str, tuple[int, int]] = {}  # label -> (priority, seq)
        self._running: str | None = None

    def _min_gap(self) -> float:
        try:
            return max(0.0, float(get_setting("min_query_interval", "10")))
        except (ValueError, TypeError):
            return 10.0

    def search(
        self,
        query: str,
        categories: list[int] | None = None,
        label: str = "unknown",
        priority: int = PRIORITY_LOW,
    ) -> list[dict]:
        with self._cond:
            self._seq += 1
            ticket = (priority, self._seq)
            self._waiters[label] = ticket
            try:
                while self._running is not None or min(self._waiters.values()) != ticket:
                    self._cond.wait()
            except BaseException:
                self._waiters.pop(label, None)
                self._cond.notify_all()
                raise

            del self._waiters[label]
            self._running = label

        try:
            wait = self._min_gap() - (time.monotonic() - self._last_request)
            if wait > 0:
                log.debug("Rate-limiting Prowlarr request, sleeping %.1fs", wait)
                time.sleep(wait)
            return _prowlarr_search_raw(query, categories)
        finally:
            self._last_request = time.monotonic()
            with self._cond:
                self._running = None
                self._cond.notify_all()

    def status(self) -> dict:
        with self._cond:
            return {
                "queued": {k for k in self._waiters},
                "running": self._running,
            }

    def is_busy(self) -> bool:
        with self._cond:
            return self._running is not None or bool(self._waiters)


_prowlarr_limiter = _ProwlarrLimiter()


def prowlarr_search(
    query: str,
    categories: list[int] | None = None,
    label: str = "unknown",
    priority: int = _ProwlarrLimiter.PRIORITY_LOW,
) -> list[dict]:
    return _prowlarr_limiter.search(query, categories, label=label, priority=priority)


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
            # sleep up to 30s, but wake if poked
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

            # Compute / fix up next_run
            next_run_iso = row["next_run"]
            if not next_run_iso:
                next_run_iso = self._compute_next(cron_expr)
                with _db_lock, get_db() as conn:
                    conn.execute("UPDATE queries SET next_run=? WHERE id=?", (next_run_iso, qid))
                    conn.commit()

            next_run_ts = datetime.fromisoformat(next_run_iso).timestamp()
            if now_ts >= next_run_ts:
                self._run_query(qid, cron_expr)

    def _run_query(self, qid: int, cron_expr: str):
        with get_db() as conn:
            row = conn.execute("SELECT * FROM queries WHERE id=?", (qid,)).fetchone()
        if not row:
            return

        log.info("[Q%d] Running: %s", qid, row["query"])
        now_iso = datetime.now(timezone.utc).isoformat()
        next_iso = self._compute_next(cron_expr)

        try:
            raw = prowlarr_search(row["query"], label=f"q:{qid}")
        except Exception as exc:
            log.error("[Q%d] Search failed: %s", qid, exc)
            with _db_lock, get_db() as conn:
                conn.execute(
                    "UPDATE queries SET last_run=?, next_run=? WHERE id=?",
                    (now_iso, next_iso, qid),
                )
                conn.commit()
            return

        # Diff against stored
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

    @staticmethod
    def _compute_next(cron_expr: str) -> str:
        cit = croniter(cron_expr, datetime.now(timezone.utc))
        return cit.get_next(datetime).isoformat()


scheduler = Scheduler()

# Initialize DB and start scheduler when the module is loaded (works with
# both `python app.py` and gunicorn importing the module).
init_db()
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
    """Run a live search and return HTML fragment for the add-query modal."""
    query = request.form.get("query", "").strip()
    if not query:
        return "<p class='preview-empty'>Enter a query above to preview results.</p>"
    try:
        raw = prowlarr_search(
            query, label="preview", priority=_ProwlarrLimiter.PRIORITY_HIGH
        )
    except Exception as exc:
        return f"<p class='preview-error'>Search failed: {exc}</p>"

    return render_template(
        "_results_fragment.html", results=raw, format_size=format_size, is_preview=True
    )


@app.route("/api/query", methods=["POST"])
def add_query():
    name = request.form.get("name", "").strip()
    query = request.form.get("query", "").strip()
    cron = request.form.get("cron", "").strip() or None

    if not name or not query:
        return "Name and query are required", 400

    now_iso = datetime.now(timezone.utc).isoformat()
    # Seed results silently
    try:
        raw = prowlarr_search(
            query, label="seed", priority=_ProwlarrLimiter.PRIORITY_HIGH
        )
    except Exception as exc:
        log.warning("Initial search failed for new query '%s': %s", query, exc)
        raw = []

    cron_expr = cron or get_setting("default_cron", "0 * * * *")
    next_iso = scheduler._compute_next(cron_expr)

    with _db_lock, get_db() as conn:
        cur = conn.execute(
            "INSERT INTO queries (name, query, cron, created_at, last_run, next_run, last_count)"
            " VALUES (?,?,?,?,?,?,?)",
            (name, query, cron, now_iso, now_iso, next_iso, len(raw)),
        )
        qid = cur.lastrowid
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
        conn.commit()

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
        # Find and run immediately in background
        with get_db() as conn:
            row = conn.execute("SELECT cron FROM queries WHERE id=?", (qid,)).fetchone()
        if row:
            cron_expr = row["cron"] or get_setting("default_cron", "0 * * * *")
            threading.Thread(
                target=scheduler._run_query, args=(qid, cron_expr), daemon=True
            ).start()
        return redirect(url_for("query_detail", qid=qid))

    if action == "update_cron":
        cron = request.form.get("cron", "").strip() or None
        next_iso = scheduler._compute_next(cron or get_setting("default_cron", "0 * * * *"))
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
    """Return the current limiter state for UI polling."""
    st = _prowlarr_limiter.status()
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
