"""Flask routes — a JSON API consumed by the React frontend."""

import logging
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone

import apprise
import requests
from flask import Blueprint, Response, jsonify, request

from callbacks import process_query_result, process_seed_result
from db import _db_lock, get_db, get_setting, set_setting
from prowlarr import (
    effective_excluded_indexers,
    format_indexer_ids,
    list_indexers,
    parse_indexer_ids,
    sanitize_url,
)
from scheduler import Scheduler, scheduler
from worker import Priority, work_queue

log = logging.getLogger("prowlarr-watcher")

bp = Blueprint("main", __name__, url_prefix="/api")

CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


# ---------------------------------------------------------------------------
# CSRF protection (double-submit cookie) and error shape
# ---------------------------------------------------------------------------
@bp.before_app_request
def _check_csrf():
    if request.method not in _MUTATING_METHODS or not request.path.startswith("/api/"):
        return None
    cookie_token = request.cookies.get(CSRF_COOKIE)
    header_token = request.headers.get(CSRF_HEADER)
    if (
        not cookie_token
        or not header_token
        or not secrets.compare_digest(cookie_token, header_token)
    ):
        return problem(403, "CSRF validation failed", "Missing or invalid X-CSRF-Token header")
    return None


@bp.after_app_request
def _add_security_headers_and_csrf(response: Response) -> Response:
    if not request.cookies.get(CSRF_COOKIE):
        response.set_cookie(
            CSRF_COOKIE,
            secrets.token_urlsafe(32),
            samesite="Strict",
            secure=request.is_secure,
        )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; "
        "font-src 'self' data:; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self';"
    )
    return response


def problem(status: int, title: str, detail: str | None = None, errors: dict | None = None):
    """RFC 9457 application/problem+json error response."""
    body: dict = {"status": status, "title": title}
    if detail:
        body["detail"] = detail
    if errors:
        body["errors"] = errors
    resp = jsonify(body)
    resp.status_code = status
    resp.headers["Content-Type"] = "application/problem+json"
    return resp


def _compute_next_or_400(cron_expr: str, field: str = "cron"):
    """Scheduler.compute_next(), or a 400 problem response if the cron expression is invalid.

    Returns (next_run_iso, None) on success, or (None, response) on failure —
    callers must check for the error before using the first value.
    """
    try:
        return Scheduler.compute_next(cron_expr), None
    except ValueError:
        return None, problem(400, "Validation failed", errors={field: ["Invalid cron expression"]})


# ---------------------------------------------------------------------------
# Serialization — DB rows / Prowlarr results -> camelCase JSON
# ---------------------------------------------------------------------------
def _serialize_query(row: sqlite3.Row) -> dict:
    excluded = row["excluded_indexers"]
    return {
        "id": row["id"],
        "name": row["name"],
        "query": row["query"],
        "cron": row["cron"],
        "enabled": bool(row["enabled"]),
        "createdAt": row["created_at"],
        "lastRun": row["last_run"],
        "nextRun": row["next_run"],
        "lastCount": row["last_count"],
        "lastError": row["last_error"],
        "lastNewResult": row["last_new_result"],
        "excludedIndexers": None if excluded is None else parse_indexer_ids(excluded),
        "note": row["note"],
    }


def _serialize_result(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "resultHash": row["result_hash"],
        "title": row["title"],
        "indexer": row["indexer"],
        "size": row["size"],
        "guid": row["guid"],
        "infoUrl": sanitize_url(row["info_url"]),
        "downloadUrl": sanitize_url(row["download_url"]),
        "seeders": row["seeders"],
        "firstSeen": row["first_seen"],
        "isNew": bool(row["is_new"]),
    }


def _serialize_preview_result(r: dict) -> dict:
    return {
        "title": r.get("title"),
        "indexer": r.get("indexer"),
        "size": r.get("size"),
        "seeders": r.get("seeders"),
        "guid": r.get("guid"),
        "infoUrl": sanitize_url(r.get("infoUrl")),
        "downloadUrl": sanitize_url(r.get("downloadUrl")),
    }


def _serialize_settings() -> dict:
    raw_interval = get_setting("min_query_interval", "10")
    try:
        min_interval = int(float(raw_interval))
    except ValueError, TypeError:
        min_interval = 10

    return {
        "prowlarrUrl": get_setting("prowlarr_url"),
        "prowlarrApiKey": get_setting("prowlarr_api_key"),
        "prowlarrExternalUrl": get_setting("prowlarr_external_url", ""),
        "defaultCron": get_setting("default_cron", "0 * * * *"),
        "minQueryInterval": min_interval,
        "maxRetries": int(get_setting("max_retries", "5")),
        "prowlarrTimeout": int(get_setting("prowlarr_timeout", "200")),
        "appriseUrls": get_setting("apprise_urls", ""),
        "defaultExcludedIndexers": parse_indexer_ids(get_setting("default_excluded_indexers", "")),
    }


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
@bp.route("/queries", methods=["GET"])
def list_queries():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM queries ORDER BY last_new_result IS NULL, last_new_result DESC, id DESC"
        ).fetchall()
    return jsonify([_serialize_query(r) for r in rows])


@bp.route("/queries", methods=["POST"])
def create_query():
    body = request.get_json(silent=True) or {}
    query_text = str(body.get("query") or "").strip()
    if not query_text:
        return problem(400, "Validation failed", errors={"query": ["Query is required"]})
    name = str(body.get("name") or "").strip() or query_text
    cron = str(body.get("cron") or "").strip() or None
    note = str(body.get("note") or "").strip() or None

    now_iso = datetime.now(timezone.utc).isoformat()
    cron_expr = cron or get_setting("default_cron", "0 * * * *")
    next_iso, err = _compute_next_or_400(cron_expr)
    if err:
        return err

    with _db_lock, get_db() as conn:
        cur = conn.execute(
            "INSERT INTO queries (name, query, cron, created_at, next_run, note)"
            " VALUES (?,?,?,?,?,?)",
            (name, query_text, cron, now_iso, next_iso, note),
        )
        qid = cur.lastrowid
        conn.commit()
        row = conn.execute("SELECT * FROM queries WHERE id=?", (qid,)).fetchone()

    work_queue.submit(
        query=query_text,
        excluded_indexers=effective_excluded_indexers(None),
        label=f"seed:{qid}",
        priority=Priority.HIGH,
        callback=lambda job, _qid=qid, _q=query_text: process_seed_result(_qid, _q, job),
    )
    scheduler.poke()
    return jsonify(_serialize_query(row)), 201


@bp.route("/queries/<int:qid>", methods=["GET"])
def get_query(qid: int):
    with get_db() as conn:
        q = conn.execute("SELECT * FROM queries WHERE id=?", (qid,)).fetchone()
        if not q:
            return problem(404, "Query not found")
        results = conn.execute(
            "SELECT * FROM results WHERE query_id=? ORDER BY first_seen DESC", (qid,)
        ).fetchall()
    data = _serialize_query(q)
    data["results"] = [_serialize_result(r) for r in results]
    return jsonify(data)


@bp.route("/queries/<int:qid>", methods=["PATCH"])
def update_query(qid: int):
    body = request.get_json(silent=True) or {}
    with get_db() as conn:
        row = conn.execute("SELECT * FROM queries WHERE id=?", (qid,)).fetchone()
    if not row:
        return problem(404, "Query not found")

    if "enabled" in body:
        with _db_lock, get_db() as conn:
            conn.execute(
                "UPDATE queries SET enabled=? WHERE id=?", (1 if body["enabled"] else 0, qid)
            )
            conn.commit()
        scheduler.poke()

    if "cron" in body:
        cron = str(body["cron"] or "").strip() or None
        next_iso, err = _compute_next_or_400(cron or get_setting("default_cron", "0 * * * *"))
        if err:
            return err
        with _db_lock, get_db() as conn:
            conn.execute("UPDATE queries SET cron=?, next_run=? WHERE id=?", (cron, next_iso, qid))
            conn.commit()
        scheduler.poke()

    if "note" in body:
        note = str(body["note"] or "").strip() or None
        with _db_lock, get_db() as conn:
            conn.execute("UPDATE queries SET note=? WHERE id=?", (note, qid))
            conn.commit()

    if "excludedIndexers" in body:
        excluded = body["excludedIndexers"]
        if excluded is not None:
            if not isinstance(excluded, list):
                return problem(
                    400,
                    "Validation failed",
                    errors={"excludedIndexers": ["Must be a list or null"]},
                )
            for x in excluded:
                if not isinstance(x, int) or isinstance(x, bool):
                    return problem(
                        400,
                        "Validation failed",
                        errors={"excludedIndexers": ["All items must be integers"]},
                    )
        value = None if excluded is None else format_indexer_ids([int(x) for x in excluded])
        with _db_lock, get_db() as conn:
            conn.execute("UPDATE queries SET excluded_indexers=? WHERE id=?", (value, qid))
            conn.commit()

    with get_db() as conn:
        updated = conn.execute("SELECT * FROM queries WHERE id=?", (qid,)).fetchone()
    return jsonify(_serialize_query(updated))


@bp.route("/queries/<int:qid>", methods=["DELETE"])
def delete_query(qid: int):
    with _db_lock, get_db() as conn:
        row = conn.execute("SELECT id FROM queries WHERE id=?", (qid,)).fetchone()
        if not row:
            return problem(404, "Query not found")
        conn.execute("DELETE FROM queries WHERE id=?", (qid,))
        conn.commit()
    scheduler.poke()
    return "", 204


@bp.route("/queries/<int:qid>/run", methods=["POST"])
def run_query(qid: int):
    with get_db() as conn:
        row = conn.execute(
            "SELECT query, cron, excluded_indexers FROM queries WHERE id=?", (qid,)
        ).fetchone()
    if not row:
        return problem(404, "Query not found")

    cron_expr = row["cron"] or get_setting("default_cron", "0 * * * *")
    work_queue.submit(
        query=row["query"],
        excluded_indexers=effective_excluded_indexers(row["excluded_indexers"]),
        label=f"run:{qid}",
        priority=Priority.HIGH,
        callback=lambda job, _qid=qid, _cron=cron_expr: process_query_result(_qid, _cron, job),
    )
    return "", 202


# ---------------------------------------------------------------------------
# Search preview / job polling
# ---------------------------------------------------------------------------
@bp.route("/search-preview", methods=["POST"])
def search_preview():
    body = request.get_json(silent=True) or {}
    query_text = str(body.get("query") or "").strip()
    if not query_text:
        return problem(400, "Validation failed", errors={"query": ["Query is required"]})
    job = work_queue.submit(
        query_text,
        excluded_indexers=effective_excluded_indexers(None),
        label=f"preview:{uuid.uuid4().hex[:8]}",
        priority=Priority.HIGH,
    )
    return jsonify({"jobId": job.job_id}), 202


@bp.route("/jobs/<job_id>", methods=["GET"])
def get_job(job_id: str):
    job = work_queue.get_job(job_id)
    if not job:
        return problem(404, "Job not found or expired")
    data: dict = {"status": job.status}
    if job.status == "error":
        data["error"] = job.error
    elif job.status in ("done",):
        data["results"] = [_serialize_preview_result(r) for r in (job.result or [])]
    return jsonify(data)


def _label_to_qid(label: str) -> str | None:
    for prefix in ("q:", "run:", "seed:"):
        if label.startswith(prefix):
            return label[len(prefix) :]
    return None


@bp.route("/queue-status", methods=["GET"])
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


# ---------------------------------------------------------------------------
# Indexers
# ---------------------------------------------------------------------------
@bp.route("/indexers", methods=["GET"])
def api_indexers():
    """Return the configured Prowlarr indexers, for populating exclusion checklists."""
    try:
        return jsonify({"indexers": list_indexers()})
    except ValueError:
        return problem(400, "Prowlarr not configured", "Prowlarr URL and API key must be set")
    except requests.exceptions.RequestException:
        log.exception("Failed to fetch indexers from Prowlarr")
        return problem(502, "Could not reach Prowlarr", "Check the URL in Settings")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
@bp.route("/settings", methods=["GET"])
def get_settings():
    return jsonify(_serialize_settings())


@bp.route("/settings", methods=["PUT"])
def put_settings():
    body = request.get_json(silent=True) or {}
    errors: dict[str, list[str]] = {}

    default_cron = str(body.get("defaultCron") or "0 * * * *").strip()
    _, err = _compute_next_or_400(default_cron, field="defaultCron")
    if err:
        return err

    prowlarr_url = str(body.get("prowlarrUrl") or "").strip()
    prowlarr_external_url = str(body.get("prowlarrExternalUrl") or "").strip()

    from urllib.parse import urlparse

    if prowlarr_url:
        parsed = urlparse(prowlarr_url)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
            errors.setdefault("prowlarrUrl", []).append(
                "Must be a valid HTTP or HTTPS URL (e.g. http://localhost:9696)"
            )

    if prowlarr_external_url:
        parsed = urlparse(prowlarr_external_url)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
            errors.setdefault("prowlarrExternalUrl", []).append(
                "Must be a valid HTTP or HTTPS URL (e.g. https://prowlarr.example.com)"
            )

    try:
        min_query_interval = float(
            body.get("minQueryInterval") if "minQueryInterval" in body else 10
        )
        if min_query_interval < 0:
            errors.setdefault("minQueryInterval", []).append("Must be non-negative")
    except ValueError, TypeError:
        errors.setdefault("minQueryInterval", []).append("Must be a valid number")

    try:
        max_retries = int(body.get("maxRetries") if "maxRetries" in body else 5)
        if max_retries < 1:
            errors.setdefault("maxRetries", []).append("Must be at least 1")
    except ValueError, TypeError:
        errors.setdefault("maxRetries", []).append("Must be a valid integer")

    try:
        prowlarr_timeout = int(body.get("prowlarrTimeout") if "prowlarrTimeout" in body else 200)
        if prowlarr_timeout < 1:
            errors.setdefault("prowlarrTimeout", []).append("Must be at least 1")
    except ValueError, TypeError:
        errors.setdefault("prowlarrTimeout", []).append("Must be a valid integer")

    raw_excluded = body.get("defaultExcludedIndexers")
    excluded_ids: list[int] = []
    if raw_excluded is not None:
        if not isinstance(raw_excluded, list):
            errors.setdefault("defaultExcludedIndexers", []).append("Must be a list")
        else:
            for x in raw_excluded:
                if not isinstance(x, int) or isinstance(x, bool):
                    errors.setdefault("defaultExcludedIndexers", []).append(
                        "All items must be integers"
                    )
                    break
                excluded_ids.append(x)

    if errors:
        return problem(400, "Validation failed", errors=errors)

    set_setting("prowlarr_url", prowlarr_url)
    set_setting("prowlarr_api_key", str(body.get("prowlarrApiKey") or "").strip())
    set_setting("prowlarr_external_url", prowlarr_external_url)
    set_setting("default_cron", default_cron)
    set_setting(
        "min_query_interval",
        str(int(min_query_interval))
        if min_query_interval.is_integer()
        else str(min_query_interval),
    )
    set_setting("max_retries", str(max_retries))
    set_setting("prowlarr_timeout", str(prowlarr_timeout))
    set_setting("apprise_urls", str(body.get("appriseUrls") or "").strip())
    set_setting("default_excluded_indexers", format_indexer_ids(excluded_ids))
    scheduler.poke()
    return jsonify(_serialize_settings())


@bp.route("/settings/test-prowlarr", methods=["POST"])
def test_prowlarr():
    body = request.get_json(silent=True) or {}
    base = str(body.get("prowlarrUrl") or "").strip().rstrip("/")
    api_key = str(body.get("prowlarrApiKey") or "").strip()
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


@bp.route("/settings/test-apprise", methods=["POST"])
def test_apprise():
    body = request.get_json(silent=True) or {}
    raw_urls = str(body.get("appriseUrls") or "")
    urls = [u.strip() for u in raw_urls.splitlines() if u.strip()]
    if not urls:
        return jsonify({"ok": False, "message": "No Apprise URLs configured"})
    ap = apprise.Apprise()
    for u in urls:
        ap.add(u)
    ok = ap.notify(title="Prowlarr Watcher — test", body="Notification delivery confirmed ✓")
    msg = "Sent!" if ok else "Delivery may have failed — check your URLs"
    return jsonify({"ok": ok, "message": msg})
