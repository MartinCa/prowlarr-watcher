"""Result processing callbacks invoked by the work queue worker."""

import logging
from datetime import datetime, timezone

from db import _db_lock, get_db
from notifications import notify_error, notify_new_results
from prowlarr import hash_result
from worker import Job

log = logging.getLogger("prowlarr-watcher")


def _insert_result(conn, qid: int, r: dict, is_new: int, now_iso: str):
    conn.execute(
        """INSERT OR IGNORE INTO results
           (query_id, result_hash, title, indexer, size, guid,
            info_url, download_url, seeders, first_seen, is_new)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            qid,
            hash_result(r),
            r.get("title"),
            r.get("indexer"),
            r.get("size"),
            r.get("guid"),
            r.get("infoUrl"),
            r.get("downloadUrl"),
            r.get("seeders"),
            now_iso,
            is_new,
        ),
    )


def process_query_result(qid: int, cron_expr: str, job: Job):
    now_iso = datetime.now(timezone.utc).isoformat()

    if job.status == "error":
        log.error("[Q%d] Search failed: %s", qid, job.error)
        with _db_lock, get_db() as conn:
            conn.execute(
                "UPDATE queries SET last_run=?, last_error=? WHERE id=?",
                (now_iso, job.error, qid),
            )
            conn.commit()
        notify_error(qid, None, "scheduled", job.error)
        return

    raw = job.result or []

    with _db_lock, get_db() as conn:
        row = conn.execute("SELECT name, query FROM queries WHERE id=?", (qid,)).fetchone()
        if not row:
            return

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
                _insert_result(conn, qid, r, 1, now_iso)

        conn.execute(
            "UPDATE queries SET last_run=?, last_count=?, last_error=NULL WHERE id=?",
            (now_iso, len(raw), qid),
        )
        conn.commit()

    log.info("[Q%d] %d total / %d new", qid, len(raw), len(new_items))

    if new_items:
        notify_new_results(row["name"], row["query"], new_items)


def process_seed_result(qid: int, query_text: str, job: Job):
    now_iso = datetime.now(timezone.utc).isoformat()

    if job.status == "error":
        log.error("[Q%d] Seed search failed after retries: %s", qid, job.error)
        with _db_lock, get_db() as conn:
            conn.execute("UPDATE queries SET last_error=? WHERE id=?", (job.error, qid))
            conn.commit()
        notify_error(qid, query_text, "seed", job.error)
        return

    raw = job.result or []

    with _db_lock, get_db() as conn:
        for r in raw:
            _insert_result(conn, qid, r, 0, now_iso)
        conn.execute(
            "UPDATE queries SET last_run=?, last_count=?, last_error=NULL WHERE id=?",
            (now_iso, len(raw), qid),
        )
        conn.commit()

    log.info("[Q%d] Seeded with %d results", qid, len(raw))
