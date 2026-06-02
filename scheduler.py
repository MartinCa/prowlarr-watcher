"""Scheduler — daemon thread that enqueues due queries."""

import logging
import threading
import time
from datetime import datetime, timezone

from croniter import croniter

from callbacks import process_query_result
from db import _db_lock, get_db, get_setting
from worker import Priority, work_queue

log = logging.getLogger("prowlarr-watcher")


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
        startup = True
        while not self._stop.is_set():
            self._wakeup.clear()
            self._tick(startup=startup)
            startup = False
            self._wakeup.wait(timeout=30)

    def _tick(self, startup: bool = False):
        now_ts = time.time()

        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, query, cron, next_run, enabled FROM queries WHERE enabled=1"
            ).fetchall()

        if startup:
            log.info("Checking for overdue queries at startup (%d enabled)", len(rows))

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
                if startup:
                    log.info(
                        "  Overdue: query %d %r due %s — queuing", qid, row["query"], next_run_iso
                    )
                next_iso = self.compute_next(cron_expr)
                with _db_lock, get_db() as conn:
                    conn.execute("UPDATE queries SET next_run=? WHERE id=?", (next_iso, qid))
                    conn.commit()

                work_queue.submit(
                    query=row["query"],
                    label=f"q:{qid}",
                    priority=Priority.LOW,
                    callback=lambda job, _qid=qid, _cron=cron_expr: process_query_result(
                        _qid, _cron, job
                    ),
                )

    @staticmethod
    def compute_next(cron_expr: str) -> str:
        cit = croniter(cron_expr, datetime.now(timezone.utc))
        return cit.get_next(datetime).isoformat()


scheduler = Scheduler()
