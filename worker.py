"""Work queue — single worker thread for all Prowlarr searches."""

import logging
import queue
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum

from db import get_setting
from prowlarr import prowlarr_search_raw

log = logging.getLogger("prowlarr-watcher")


class Priority(IntEnum):
    HIGH = 0  # interactive: preview, seed, run-now
    LOW = 1  # scheduled queries


@dataclass
class Job:
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    query: str = ""
    categories: list[int] | None = None
    excluded_indexers: list[int] | None = None
    label: str = "unknown"
    priority: Priority = Priority.LOW
    callback: Callable[["Job"], None] | None = None
    status: str = "queued"  # queued -> running -> done | error
    result: list[dict] | None = None
    error: str | None = None
    attempt: int = 1
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

    @staticmethod
    def _max_retries() -> int:
        try:
            return max(1, int(get_setting("max_retries", "5")))
        except Exception:
            return 5

    def submit(
        self,
        query: str,
        categories: list[int] | None = None,
        excluded_indexers: list[int] | None = None,
        label: str = "unknown",
        priority: Priority = Priority.LOW,
        callback: Callable[[Job], None] | None = None,
        attempt: int = 1,
    ) -> Job:
        with self._lock:
            if label in self._active_labels:
                for j in self._jobs.values():
                    if j.label == label and j.status in ("queued", "running"):
                        return j

            self._seq += 1
            job = Job(
                query=query,
                categories=categories,
                excluded_indexers=excluded_indexers,
                label=label,
                priority=priority,
                callback=callback,
                attempt=attempt,
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
                job.result = prowlarr_search_raw(job.query, job.categories, job.excluded_indexers)
                job.status = "done"
            except Exception as exc:
                job.error = f"{type(exc).__name__}: {exc}"
                max_ret = self._max_retries()
                if job.attempt < max_ret:
                    log.warning(
                        "Search failed for %r (attempt %d/%d), retrying",
                        job.label,
                        job.attempt,
                        max_ret,
                        exc_info=True,
                    )
                    job.status = "retrying"
                else:
                    job.status = "error"
                    log.error(
                        "Search failed for %r after %d attempts",
                        job.label,
                        max_ret,
                        exc_info=True,
                    )
            finally:
                last_request = time.monotonic()
                with self._lock:
                    self._running = None
                    if job.status != "retrying":
                        self._active_labels.discard(job.label)

            if job.status == "retrying":
                self.submit(
                    query=job.query,
                    categories=job.categories,
                    excluded_indexers=job.excluded_indexers,
                    label=job.label,
                    priority=job.priority,
                    callback=job.callback,
                    attempt=job.attempt + 1,
                )
                self._pq.task_done()
                continue

            self._pq.task_done()

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
