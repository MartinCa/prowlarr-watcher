"""Result processing callbacks invoked by the work queue worker."""

import logging
from datetime import datetime, timezone

from db import _db_lock, get_db
from notifications import notify_error, notify_new_results
from prowlarr import hash_result, sanitize_url, unhealthy_indexer_names
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
            sanitize_url(r.get("infoUrl")),
            sanitize_url(r.get("downloadUrl")),
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

    # Results from indexers that failed during the search must not be treated as removed.
    # If the status can't be fetched we can't tell, so skip pruning this run.
    try:
        unhealthy = unhealthy_indexer_names(job.searched_at or datetime.now(timezone.utc))
    except Exception:
        log.warning("[Q%d] Could not read Prowlarr indexer status; not pruning", qid, exc_info=True)
        unhealthy = None

    with _db_lock, get_db() as conn:
        row = conn.execute(
            "SELECT name, query, note, audiobook, ebook FROM queries WHERE id=?", (qid,)
        ).fetchone()
        if not row:
            return

        stored = {
            r["result_hash"]: r["indexer"]
            for r in conn.execute(
                "SELECT result_hash, indexer FROM results WHERE query_id=?", (qid,)
            ).fetchall()
        }
        seen = stored.keys()

        current = set()
        new_items = []
        for r in raw:
            h = hash_result(r)
            current.add(h)
            if h not in seen:
                new_items.append(r)
                _insert_result(conn, qid, r, 1, now_iso)

        # Drop results Prowlarr no longer returns, except those of indexers that failed or
        # are disabled (their absence says nothing). A result that merely left an indexer
        # (e.g. a freeleech period ending) is gone from a healthy indexer and is removed.
        gone = (
            []
            if unhealthy is None
            else [h for h, ix in stored.items() if h not in current and ix not in unhealthy]
        )
        if gone:
            conn.executemany(
                "DELETE FROM results WHERE query_id=? AND result_hash=?",
                [(qid, h) for h in gone],
            )

        if new_items:
            conn.execute(
                "UPDATE queries SET last_run=?, last_count=?, last_error=NULL,"
                " last_new_result=? WHERE id=?",
                (now_iso, len(raw), now_iso, qid),
            )
        else:
            conn.execute(
                "UPDATE queries SET last_run=?, last_count=?, last_error=NULL WHERE id=?",
                (now_iso, len(raw), qid),
            )
        conn.commit()

    log.info("[Q%d] %d total / %d new / %d removed", qid, len(raw), len(new_items), len(gone))

    if new_items:
        notify_new_results(
            row["name"],
            row["query"],
            new_items,
            row["note"],
            bool(row["audiobook"]),
            bool(row["ebook"]),
        )


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
