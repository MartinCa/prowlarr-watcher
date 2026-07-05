"""Flask routes — pages and API endpoints."""

import logging
import uuid
from datetime import datetime, timezone

import apprise
import requests
from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from markupsafe import escape

from callbacks import process_query_result, process_seed_result
from db import _db_lock, get_db, get_setting, set_setting
from prowlarr import (
    effective_excluded_indexers,
    format_indexer_ids,
    format_size,
    list_indexers,
    parse_indexer_ids,
    prowlarr_link_base,
)
from scheduler import Scheduler, scheduler
from worker import Priority, work_queue

log = logging.getLogger("prowlarr-watcher")

bp = Blueprint("main", __name__)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@bp.route("/")
def index():
    with get_db() as conn:
        queries = conn.execute("SELECT * FROM queries ORDER BY id DESC").fetchall()
        default_cron = get_setting("default_cron", "0 * * * *")
    return render_template("index.html", queries=queries, default_cron=default_cron)


@bp.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        set_setting("prowlarr_url", request.form.get("prowlarr_url", "").strip())
        set_setting("prowlarr_api_key", request.form.get("prowlarr_api_key", "").strip())
        set_setting("prowlarr_external_url", request.form.get("prowlarr_external_url", "").strip())
        set_setting("default_cron", request.form.get("default_cron", "0 * * * *").strip())
        set_setting(
            "min_query_interval",
            request.form.get("min_query_interval", "10").strip(),
        )
        set_setting("max_retries", request.form.get("max_retries", "5").strip())
        set_setting("prowlarr_timeout", request.form.get("prowlarr_timeout", "200").strip())
        set_setting("apprise_urls", request.form.get("apprise_urls", "").strip())
        excluded_ids = [int(x) for x in request.form.getlist("excluded_indexers")]
        set_setting("default_excluded_indexers", format_indexer_ids(excluded_ids))
        scheduler.poke()
        return redirect(url_for("main.settings") + "?saved=1")

    return render_template(
        "settings.html",
        prowlarr_url=get_setting("prowlarr_url"),
        prowlarr_api_key=get_setting("prowlarr_api_key"),
        prowlarr_external_url=get_setting("prowlarr_external_url", ""),
        default_cron=get_setting("default_cron"),
        min_query_interval=get_setting("min_query_interval", "10"),
        max_retries=get_setting("max_retries", "5"),
        prowlarr_timeout=get_setting("prowlarr_timeout", "200"),
        apprise_urls=get_setting("apprise_urls"),
        default_excluded_indexers=parse_indexer_ids(get_setting("default_excluded_indexers", "")),
        saved=request.args.get("saved"),
    )


@bp.route("/query/<int:qid>")
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
        prowlarr_url=prowlarr_link_base(),
        default_excluded_indexers=parse_indexer_ids(get_setting("default_excluded_indexers", "")),
        query_excluded_indexers=(
            None if q["excluded_indexers"] is None else parse_indexer_ids(q["excluded_indexers"])
        ),
    )


# ---------------------------------------------------------------------------
# API / htmx actions
# ---------------------------------------------------------------------------
@bp.route("/api/search-preview", methods=["POST"])
def search_preview():
    """Submit a preview search and return a polling fragment."""
    query_text = request.form.get("query", "").strip()
    if not query_text:
        return "<p class='preview-empty'>Enter a query above to preview results.</p>"
    job = work_queue.submit(
        query_text,
        excluded_indexers=effective_excluded_indexers(None),
        label=f"preview:{uuid.uuid4().hex[:8]}",
        priority=Priority.HIGH,
    )
    safe_id = escape(job.job_id)
    return (
        f'<div hx-get="/api/job/{safe_id}/preview"'
        f' hx-trigger="load, every 1s"'
        f' hx-swap="outerHTML">'
        f'<span style="font-family:var(--mono);font-size:12px;color:var(--muted)">'
        f"searching Prowlarr…</span></div>"
    )


@bp.route("/api/job/<job_id>/preview")
def job_preview(job_id: str):
    """Poll endpoint for preview results."""
    job = work_queue.get_job(job_id)
    if not job:
        return "<p class='preview-error'>Job expired or not found.</p>"
    safe_id = escape(job_id)
    if job.status in ("queued", "running"):
        status_text = (
            "queued — waiting for other searches…"
            if job.status == "queued"
            else "searching Prowlarr…"
        )
        return (
            f'<div hx-get="/api/job/{safe_id}/preview"'
            f' hx-trigger="every 1s"'
            f' hx-swap="outerHTML">'
            f'<span style="font-family:var(--mono);font-size:12px;color:'
            f'{"var(--yellow)" if job.status == "queued" else "var(--muted)"}">'
            f"{status_text}</span></div>"
        )
    if job.status == "error":
        return f"<p class='preview-error'>Search failed: {escape(job.error or '')}</p>"
    return render_template(
        "_results_fragment.html", results=job.result, format_size=format_size, is_preview=True
    )


@bp.route("/api/query", methods=["POST"])
def add_query():
    query_text = request.form.get("query", "").strip()
    name = request.form.get("name", "").strip() or query_text
    cron = request.form.get("cron", "").strip() or None

    if not query_text:
        return "Query is required", 400

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
        excluded_indexers=effective_excluded_indexers(None),
        label=f"seed:{qid}",
        priority=Priority.HIGH,
        callback=lambda job, _qid=qid, _q=query_text: process_seed_result(_qid, _q, job),
    )
    scheduler.poke()
    return redirect(url_for("main.index"))


@bp.route("/api/query/<int:qid>", methods=["POST"])
def update_query(qid: int):
    action = request.form.get("action")

    if action == "delete":
        with _db_lock, get_db() as conn:
            conn.execute("DELETE FROM queries WHERE id=?", (qid,))
            conn.commit()
        scheduler.poke()
        return redirect(url_for("main.index"))

    if action == "toggle":
        with _db_lock, get_db() as conn:
            row = conn.execute("SELECT enabled FROM queries WHERE id=?", (qid,)).fetchone()
            new_val = 0 if row["enabled"] else 1
            conn.execute("UPDATE queries SET enabled=? WHERE id=?", (new_val, qid))
            conn.commit()
        scheduler.poke()
        return redirect(url_for("main.index"))

    if action == "run_now":
        with get_db() as conn:
            row = conn.execute(
                "SELECT query, cron, excluded_indexers FROM queries WHERE id=?", (qid,)
            ).fetchone()
        if row:
            cron_expr = row["cron"] or get_setting("default_cron", "0 * * * *")
            work_queue.submit(
                query=row["query"],
                excluded_indexers=effective_excluded_indexers(row["excluded_indexers"]),
                label=f"run:{qid}",
                priority=Priority.HIGH,
                callback=lambda job, _qid=qid, _cron=cron_expr: process_query_result(
                    _qid, _cron, job
                ),
            )
        return redirect(url_for("main.query_detail", qid=qid))

    if action == "update_indexers":
        if request.form.get("override"):
            excluded_ids = [int(x) for x in request.form.getlist("excluded_indexers")]
            value = format_indexer_ids(excluded_ids)
        else:
            value = None
        with _db_lock, get_db() as conn:
            conn.execute("UPDATE queries SET excluded_indexers=? WHERE id=?", (value, qid))
            conn.commit()
        return redirect(url_for("main.query_detail", qid=qid))

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
        return redirect(url_for("main.query_detail", qid=qid))

    return "Unknown action", 400


def _label_to_qid(label: str) -> str | None:
    for prefix in ("q:", "run:", "seed:"):
        if label.startswith(prefix):
            return label[len(prefix) :]
    return None


@bp.route("/api/queue-status")
def queue_status():
    """Return the current work queue state for UI polling."""
    st = work_queue.status()
    query_states: dict[str, str] = {}
    for label in st["queued"]:
        qid = _label_to_qid(label)
        if qid:
            query_states[qid] = "queued"
    running = st["running"] or ""
    qid = _label_to_qid(running)
    if qid:
        query_states[qid] = "running"
    preview_state = None
    if any(lab.startswith("preview:") for lab in st["queued"]):
        preview_state = "queued"
    elif running.startswith("preview:"):
        preview_state = "running"
    return jsonify({"queries": query_states, "preview": preview_state})


@bp.route("/api/indexers")
def api_indexers():
    """Return the configured Prowlarr indexers, for populating exclusion checklists."""
    try:
        return jsonify({"ok": True, "indexers": list_indexers()})
    except ValueError as exc:
        return jsonify({"ok": False, "message": str(exc)})
    except requests.exceptions.RequestException:
        log.exception("Failed to fetch indexers from Prowlarr")
        return jsonify({"ok": False, "message": "Could not reach Prowlarr — check Settings"})


@bp.route("/api/test-prowlarr", methods=["POST"])
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
    except Exception:
        log.exception("Unexpected error testing Prowlarr connection")
        return jsonify({"ok": False, "message": "Unexpected error — check server logs"})


@bp.route("/api/test-apprise", methods=["POST"])
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
@bp.app_template_filter("timeago")
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


@bp.app_template_filter("fmt_size")
def fmt_size_filter(size: int | None) -> str:
    return format_size(size)
