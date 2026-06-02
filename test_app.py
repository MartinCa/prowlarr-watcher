"""Comprehensive tests for Prowlarr Watcher."""

import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

# Point DATA_DIR to a temp directory before importing app (which runs init_db at import time)
_tmpdir = tempfile.mkdtemp()
os.environ["DATA_DIR"] = _tmpdir

# Patch the background threads so they don't start during tests
with (
    patch("threading.Thread.start"),
):
    import app as app_mod
    import callbacks
    import db
    import notifications
    import prowlarr
    import routes
    import scheduler as scheduler_mod
    import worker


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    """Give each test a fresh SQLite database."""
    db_path = tmp_path / "watcher.db"
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.init_db()
    yield


@pytest.fixture()
def client():
    app_mod.app.config["TESTING"] = True
    with app_mod.app.test_client() as c:
        yield c


def _configure_prowlarr():
    """Set valid Prowlarr settings so searches don't fail on missing config."""
    db.set_setting("prowlarr_url", "http://localhost:9696")
    db.set_setting("prowlarr_api_key", "test-key-123")


def _insert_query(name="Test", query="ubuntu", cron=None, enabled=1):
    """Insert a query directly into the DB and return its id."""
    now = datetime.now(timezone.utc).isoformat()
    next_iso = scheduler_mod.Scheduler.compute_next(cron or "0 * * * *")
    with db._db_lock, db.get_db() as conn:
        cur = conn.execute(
            "INSERT INTO queries (name, query, cron, enabled,"
            " created_at, last_run, next_run, last_count)"
            " VALUES (?,?,?,?,?,?,?,0)",
            (name, query, cron, enabled, now, now, next_iso),
        )
        conn.commit()
        return cur.lastrowid


def _insert_result(query_id, title="item1", guid=None):
    """Insert a result directly into the DB."""
    guid = guid or f"guid-{title}"
    h = prowlarr.hash_result({"guid": guid, "title": title})
    now = datetime.now(timezone.utc).isoformat()
    with db._db_lock, db.get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO results "
            "(query_id, result_hash, title, indexer, size, guid, first_seen, is_new) "
            "VALUES (?,?,?,?,?,?,?,1)",
            (query_id, h, title, "test-indexer", 1024, guid, now),
        )
        conn.commit()


SAMPLE_RESULTS = [
    {
        "title": "Ubuntu 24.04 LTS",
        "indexer": "TestIndexer",
        "size": 4_000_000_000,
        "guid": "guid-ubuntu-2404",
        "infoUrl": "https://example.com/ubuntu",
        "downloadUrl": "https://example.com/ubuntu.torrent",
        "seeders": 150,
    },
    {
        "title": "Ubuntu 23.10",
        "indexer": "OtherIndexer",
        "size": 3_500_000_000,
        "guid": "guid-ubuntu-2310",
        "infoUrl": None,
        "downloadUrl": None,
        "seeders": 5,
    },
]


# ===========================================================================
# Unit tests — pure functions
# ===========================================================================
class TestHashResult:
    def test_uses_guid_when_present(self):
        r = {"guid": "abc123", "title": "Something", "size": 999}
        h = prowlarr.hash_result(r)
        assert len(h) == 16
        # Same guid → same hash
        assert h == prowlarr.hash_result({"guid": "abc123"})

    def test_falls_back_to_title_and_size(self):
        r = {"title": "Something", "size": 999}
        h = prowlarr.hash_result(r)
        assert len(h) == 16
        assert h == prowlarr.hash_result({"title": "Something", "size": 999})

    def test_different_guids_differ(self):
        assert prowlarr.hash_result({"guid": "a"}) != prowlarr.hash_result({"guid": "b"})

    def test_empty_guid_falls_back(self):
        r = {"guid": "", "title": "T", "size": 1}
        h = prowlarr.hash_result(r)
        assert h == prowlarr.hash_result({"title": "T", "size": 1})


class TestFormatSize:
    def test_none(self):
        assert prowlarr.format_size(None) == "—"

    def test_zero(self):
        assert prowlarr.format_size(0) == "—"

    def test_bytes(self):
        assert prowlarr.format_size(512) == "512.0 B"

    def test_kilobytes(self):
        assert prowlarr.format_size(10_240) == "10.0 KB"

    def test_megabytes(self):
        assert prowlarr.format_size(5 * 1024 * 1024) == "5.0 MB"

    def test_gigabytes(self):
        assert prowlarr.format_size(2 * 1024**3) == "2.0 GB"

    def test_terabytes(self):
        assert prowlarr.format_size(3 * 1024**4) == "3.0 TB"


class TestTimeagoFilter:
    def test_none_returns_never(self):
        with app_mod.app.app_context():
            assert routes.timeago_filter(None) == "never"

    def test_recent_past(self):
        with app_mod.app.app_context():
            now = datetime.now(timezone.utc)
            assert routes.timeago_filter(now.isoformat()) == "just now"

    def test_future(self):
        with app_mod.app.app_context():
            from datetime import timedelta

            future = datetime.now(timezone.utc) + timedelta(hours=2)
            result = routes.timeago_filter(future.isoformat())
            assert result.startswith("in ")

    def test_invalid_returns_raw(self):
        with app_mod.app.app_context():
            assert routes.timeago_filter("not-a-date") == "not-a-date"


# ===========================================================================
# Database tests
# ===========================================================================
class TestDatabase:
    def test_init_db_creates_tables(self):
        with db.get_db() as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert "settings" in tables
        assert "queries" in tables
        assert "results" in tables

    def test_default_settings_exist(self):
        assert db.get_setting("prowlarr_url") == "http://prowlarr:9696"
        assert db.get_setting("prowlarr_api_key") == ""
        assert db.get_setting("default_cron") == "0 * * * *"
        assert db.get_setting("min_query_interval") == "10"

    def test_get_setting_default(self):
        assert db.get_setting("nonexistent", "fallback") == "fallback"

    def test_set_setting_insert_and_update(self):
        db.set_setting("test_key", "value1")
        assert db.get_setting("test_key") == "value1"
        db.set_setting("test_key", "value2")
        assert db.get_setting("test_key") == "value2"

    def test_cascade_delete(self):
        qid = _insert_query()
        _insert_result(qid, title="r1")
        with db.get_db() as conn:
            assert (
                conn.execute("SELECT COUNT(*) FROM results WHERE query_id=?", (qid,)).fetchone()[0]
                == 1
            )
        with db._db_lock, db.get_db() as conn:
            conn.execute("DELETE FROM queries WHERE id=?", (qid,))
            conn.commit()
        with db.get_db() as conn:
            assert (
                conn.execute("SELECT COUNT(*) FROM results WHERE query_id=?", (qid,)).fetchone()[0]
                == 0
            )

    def test_unique_result_constraint(self):
        qid = _insert_query()
        _insert_result(qid, title="dup", guid="same-guid")
        _insert_result(qid, title="dup", guid="same-guid")  # should be ignored
        with db.get_db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM results WHERE query_id=?", (qid,)
            ).fetchone()[0]
        assert count == 1


# ===========================================================================
# Prowlarr API helper tests
# ===========================================================================
class TestProwlarrSearchRaw:
    def test_raises_without_config(self):
        db.set_setting("prowlarr_api_key", "")
        with pytest.raises(ValueError, match="configured in Settings"):
            prowlarr.prowlarr_search_raw("test")

    @patch("prowlarr.requests.get")
    def test_successful_search(self, mock_get):
        _configure_prowlarr()
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_RESULTS
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        results = prowlarr.prowlarr_search_raw("ubuntu")

        assert len(results) == 2
        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args
        assert "X-Api-Key" in call_kwargs.kwargs.get("headers", call_kwargs[1].get("headers", {}))

    @patch("prowlarr.requests.get")
    def test_search_with_categories(self, mock_get):
        _configure_prowlarr()
        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        prowlarr.prowlarr_search_raw("test", categories=[2000, 5000])

        params = mock_get.call_args.kwargs.get("params", mock_get.call_args[1].get("params", {}))
        assert params["categories"] == [2000, 5000]

    @patch("prowlarr.requests.get")
    def test_http_error_propagates(self, mock_get):
        _configure_prowlarr()
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("HTTP 500")
        mock_get.return_value = mock_resp

        with pytest.raises(Exception, match="HTTP 500"):
            prowlarr.prowlarr_search_raw("test")


# ===========================================================================
# Job and WorkQueue tests
# ===========================================================================
class TestJob:
    def test_ordering_by_priority(self):
        high = worker.Job(priority=worker.Priority.HIGH, _seq=2)
        low = worker.Job(priority=worker.Priority.LOW, _seq=1)
        assert high < low

    def test_ordering_by_seq_within_same_priority(self):
        first = worker.Job(priority=worker.Priority.LOW, _seq=1)
        second = worker.Job(priority=worker.Priority.LOW, _seq=2)
        assert first < second

    def test_default_status_is_queued(self):
        j = worker.Job()
        assert j.status == "queued"
        assert j.result is None
        assert j.error is None


class TestWorkQueue:
    def _make_queue(self):
        """Create a WorkQueue without starting the worker thread."""
        wq = worker.WorkQueue()
        return wq

    def test_submit_returns_job(self):
        wq = self._make_queue()
        job = wq.submit("ubuntu", label="test")
        assert job.status == "queued"
        assert job.query == "ubuntu"
        assert job.label == "test"

    def test_submit_deduplicates_active_labels(self):
        wq = self._make_queue()
        j1 = wq.submit("ubuntu", label="q:1")
        j2 = wq.submit("ubuntu", label="q:1")
        assert j1.job_id == j2.job_id

    def test_get_job(self):
        wq = self._make_queue()
        job = wq.submit("test", label="x")
        assert wq.get_job(job.job_id) is job
        assert wq.get_job("nonexistent") is None

    def test_status_shows_queued(self):
        wq = self._make_queue()
        wq.submit("test", label="q:5")
        st = wq.status()
        assert "q:5" in st["queued"]
        assert st["running"] is None

    @patch("worker.prowlarr_search_raw")
    def test_worker_processes_job(self, mock_search):
        _configure_prowlarr()
        mock_search.return_value = [{"title": "result1", "guid": "g1"}]

        wq = self._make_queue()
        # Set min gap to 0 for fast tests
        wq._min_gap = lambda: 0.0

        job = wq.submit("ubuntu", label="test-run")

        # Run worker in a thread, it will process the one job then block on next get()
        worker_thread = threading.Thread(target=wq._worker, daemon=True)
        worker_thread.start()

        # Wait for job to complete
        for _ in range(50):
            if job.status in ("done", "error"):
                break
            time.sleep(0.05)

        assert job.status == "done"
        assert job.result == [{"title": "result1", "guid": "g1"}]
        mock_search.assert_called_once_with("ubuntu", None)

    @patch("worker.prowlarr_search_raw")
    def test_worker_handles_search_error(self, mock_search):
        _configure_prowlarr()
        mock_search.side_effect = ConnectionError("refused")

        wq = self._make_queue()
        wq._min_gap = lambda: 0.0
        wq._max_retries = lambda: 1  # no retries — fail immediately

        job = wq.submit("fail", label="err-test")

        worker_thread = threading.Thread(target=wq._worker, daemon=True)
        worker_thread.start()

        for _ in range(50):
            if job.status in ("done", "error"):
                break
            time.sleep(0.05)

        assert job.status == "error"
        assert "refused" in job.error

    @patch("worker.prowlarr_search_raw")
    def test_worker_retries_on_error(self, mock_search):
        _configure_prowlarr()
        mock_search.side_effect = [ConnectionError("refused"), [{"title": "ok"}]]

        wq = self._make_queue()
        wq._min_gap = lambda: 0.0
        wq._max_retries = lambda: 3

        callback_results = []
        wq.submit(
            "retry-test",
            label="retry-test",
            callback=lambda j: callback_results.append(j.status),
        )

        worker_thread = threading.Thread(target=wq._worker, daemon=True)
        worker_thread.start()

        for _ in range(100):
            if callback_results:
                break
            time.sleep(0.05)

        assert callback_results == ["done"]

    @patch("worker.prowlarr_search_raw")
    def test_worker_calls_callback(self, mock_search):
        _configure_prowlarr()
        mock_search.return_value = []

        wq = self._make_queue()
        wq._min_gap = lambda: 0.0

        callback = MagicMock()
        job = wq.submit("test", label="cb-test", callback=callback)

        worker_thread = threading.Thread(target=wq._worker, daemon=True)
        worker_thread.start()

        for _ in range(50):
            if job.status in ("done", "error"):
                break
            time.sleep(0.05)

        assert job.status == "done"
        callback.assert_called_once_with(job)

    @patch("worker.prowlarr_search_raw")
    def test_worker_survives_callback_exception(self, mock_search):
        _configure_prowlarr()
        mock_search.return_value = []

        wq = self._make_queue()
        wq._min_gap = lambda: 0.0

        bad_callback = MagicMock(side_effect=RuntimeError("boom"))
        job1 = wq.submit("first", label="cb-err", callback=bad_callback)
        job2 = wq.submit("second", label="after-err")

        worker_thread = threading.Thread(target=wq._worker, daemon=True)
        worker_thread.start()

        for _ in range(100):
            if job2.status in ("done", "error"):
                break
            time.sleep(0.05)

        assert job1.status == "done"
        assert job2.status == "done"

    @patch("worker.prowlarr_search_raw")
    def test_priority_ordering(self, mock_search):
        _configure_prowlarr()
        call_order = []
        mock_search.side_effect = lambda q, c=None: (call_order.append(q), [])[1]

        wq = self._make_queue()
        wq._min_gap = lambda: 0.0

        # Submit LOW first, then HIGH — HIGH should run first
        low_job = wq.submit("low-query", label="low", priority=worker.Priority.LOW)
        high_job = wq.submit("high-query", label="high", priority=worker.Priority.HIGH)

        worker_thread = threading.Thread(target=wq._worker, daemon=True)
        worker_thread.start()

        for _ in range(100):
            if low_job.status in ("done", "error") and high_job.status in ("done", "error"):
                break
            time.sleep(0.05)

        assert call_order[0] == "high-query"
        assert call_order[1] == "low-query"

    def test_cleanup_removes_expired_jobs(self):
        wq = self._make_queue()
        job = wq.submit("test", label="expire-test")
        # Manually mark done and backdate
        job.status = "done"
        job.created_at = time.monotonic() - 600
        with wq._lock:
            wq._active_labels.discard(job.label)

        wq._cleanup()
        assert wq.get_job(job.job_id) is None

    def test_cleanup_keeps_fresh_jobs(self):
        wq = self._make_queue()
        job = wq.submit("test", label="keep-test")
        job.status = "done"
        with wq._lock:
            wq._active_labels.discard(job.label)

        wq._cleanup()
        assert wq.get_job(job.job_id) is not None

    def test_min_gap_reads_setting(self):
        db.set_setting("min_query_interval", "5")
        wq = self._make_queue()
        assert wq._min_gap() == 5.0

    def test_min_gap_handles_invalid(self):
        db.set_setting("min_query_interval", "not-a-number")
        wq = self._make_queue()
        assert wq._min_gap() == 10.0

    def test_min_gap_clamps_negative(self):
        db.set_setting("min_query_interval", "-5")
        wq = self._make_queue()
        assert wq._min_gap() == 0.0


# ===========================================================================
# Result processing callback tests
# ===========================================================================
class TestProcessQueryResult:
    def test_new_results_inserted(self):
        _configure_prowlarr()
        qid = _insert_query(name="Q1", query="ubuntu")
        job = worker.Job(status="done", result=SAMPLE_RESULTS)

        callbacks.process_query_result(qid, "0 * * * *", job)

        with db.get_db() as conn:
            results = conn.execute("SELECT * FROM results WHERE query_id=?", (qid,)).fetchall()
        assert len(results) == 2
        assert all(r["is_new"] == 1 for r in results)

    def test_duplicate_results_skipped(self):
        _configure_prowlarr()
        qid = _insert_query()
        _insert_result(qid, title="Ubuntu 24.04 LTS", guid="guid-ubuntu-2404")

        job = worker.Job(status="done", result=SAMPLE_RESULTS)
        callbacks.process_query_result(qid, "0 * * * *", job)

        with db.get_db() as conn:
            results = conn.execute("SELECT * FROM results WHERE query_id=?", (qid,)).fetchall()
        # 1 existing + 1 new (the 23.10 one)
        assert len(results) == 2

    def test_updates_last_run_and_count(self):
        qid = _insert_query()
        job = worker.Job(status="done", result=SAMPLE_RESULTS)
        callbacks.process_query_result(qid, "0 * * * *", job)

        with db.get_db() as conn:
            q = conn.execute(
                "SELECT last_run, last_count FROM queries WHERE id=?", (qid,)
            ).fetchone()
        assert q["last_run"] is not None
        assert q["last_count"] == 2

    def test_error_job_updates_timestamps(self):
        qid = _insert_query()
        job = worker.Job(status="error", error="connection refused")
        callbacks.process_query_result(qid, "0 * * * *", job)

        with db.get_db() as conn:
            q = conn.execute("SELECT last_run, next_run FROM queries WHERE id=?", (qid,)).fetchone()
        assert q["last_run"] is not None

    def test_deleted_query_no_crash(self):
        job = worker.Job(status="done", result=SAMPLE_RESULTS)
        # qid 9999 doesn't exist
        callbacks.process_query_result(9999, "0 * * * *", job)

    @patch("callbacks.notify_new_results")
    def test_notifies_on_new_results(self, mock_notify):
        qid = _insert_query(name="MyQuery", query="ubuntu")
        job = worker.Job(status="done", result=SAMPLE_RESULTS)
        callbacks.process_query_result(qid, "0 * * * *", job)

        mock_notify.assert_called_once()
        call_args = mock_notify.call_args[0]
        assert call_args[0] == "MyQuery"
        assert len(call_args[2]) == 2

    @patch("callbacks.notify_new_results")
    def test_no_notification_when_no_new_results(self, mock_notify):
        qid = _insert_query()
        # Pre-insert all results
        for r in SAMPLE_RESULTS:
            _insert_result(qid, title=r["title"], guid=r["guid"])

        job = worker.Job(status="done", result=SAMPLE_RESULTS)
        callbacks.process_query_result(qid, "0 * * * *", job)

        mock_notify.assert_not_called()


class TestProcessSeedResult:
    def test_seed_inserts_as_not_new(self):
        qid = _insert_query()
        job = worker.Job(status="done", result=SAMPLE_RESULTS)
        callbacks.process_seed_result(qid, "test", job)

        with db.get_db() as conn:
            results = conn.execute("SELECT * FROM results WHERE query_id=?", (qid,)).fetchall()
        assert len(results) == 2
        assert all(r["is_new"] == 0 for r in results)

    def test_seed_updates_last_run_and_count(self):
        qid = _insert_query()
        job = worker.Job(status="done", result=SAMPLE_RESULTS)
        callbacks.process_seed_result(qid, "test", job)

        with db.get_db() as conn:
            q = conn.execute(
                "SELECT last_run, last_count FROM queries WHERE id=?", (qid,)
            ).fetchone()
        assert q["last_run"] is not None
        assert q["last_count"] == 2

    @patch("callbacks.notify_error")
    def test_seed_error_stores_error_and_notifies(self, mock_notify_err):
        qid = _insert_query()
        job = worker.Job(status="error", error="boom")
        callbacks.process_seed_result(qid, "test", job)

        with db.get_db() as conn:
            q = conn.execute("SELECT last_error FROM queries WHERE id=?", (qid,)).fetchone()
        assert q["last_error"] == "boom"

        mock_notify_err.assert_called_once()
        assert mock_notify_err.call_args[0][3] == "boom"

        with db.get_db() as conn:
            results = conn.execute("SELECT * FROM results WHERE query_id=?", (qid,)).fetchall()
        assert len(results) == 0


# ===========================================================================
# Scheduler tests
# ===========================================================================
class TestScheduler:
    def test_compute_next_returns_iso(self):
        result = scheduler_mod.Scheduler.compute_next("*/5 * * * *")
        dt = datetime.fromisoformat(result)
        assert dt > datetime.now(timezone.utc)

    @patch.object(worker.work_queue, "submit")
    def test_tick_enqueues_due_queries(self, mock_submit):
        mock_submit.return_value = worker.Job()
        # Insert a query with next_run in the past
        now = datetime.now(timezone.utc).isoformat()
        with db._db_lock, db.get_db() as conn:
            conn.execute(
                "INSERT INTO queries (name, query, cron, enabled, created_at, next_run) "
                "VALUES (?,?,?,1,?,?)",
                ("Test", "ubuntu", "*/5 * * * *", now, "2020-01-01T00:00:00+00:00"),
            )
            conn.commit()

        sched = scheduler_mod.Scheduler()
        sched._tick()

        mock_submit.assert_called_once()
        call_kwargs = mock_submit.call_args
        assert call_kwargs.kwargs["priority"] == worker.Priority.LOW

    @patch.object(worker.work_queue, "submit")
    def test_tick_skips_future_queries(self, mock_submit):
        # Insert a query with next_run in the future
        future = "2099-01-01T00:00:00+00:00"
        now = datetime.now(timezone.utc).isoformat()
        with db._db_lock, db.get_db() as conn:
            conn.execute(
                "INSERT INTO queries (name, query, cron, enabled, created_at, next_run) "
                "VALUES (?,?,?,1,?,?)",
                ("Test", "ubuntu", "0 * * * *", now, future),
            )
            conn.commit()

        sched = scheduler_mod.Scheduler()
        sched._tick()

        mock_submit.assert_not_called()

    @patch.object(worker.work_queue, "submit")
    def test_tick_skips_disabled_queries(self, mock_submit):
        _insert_query(enabled=0)
        sched = scheduler_mod.Scheduler()
        sched._tick()
        mock_submit.assert_not_called()

    @patch.object(worker.work_queue, "submit")
    def test_tick_advances_next_run(self, mock_submit):
        mock_submit.return_value = worker.Job()
        now = datetime.now(timezone.utc).isoformat()
        with db._db_lock, db.get_db() as conn:
            cur = conn.execute(
                "INSERT INTO queries (name, query, cron, enabled, created_at, next_run) "
                "VALUES (?,?,?,1,?,?)",
                ("Test", "ubuntu", "*/5 * * * *", now, "2020-01-01T00:00:00+00:00"),
            )
            qid = cur.lastrowid
            conn.commit()

        sched = scheduler_mod.Scheduler()
        sched._tick()

        with db.get_db() as conn:
            q = conn.execute("SELECT next_run FROM queries WHERE id=?", (qid,)).fetchone()
        new_next = datetime.fromisoformat(q["next_run"])
        assert new_next > datetime.now(timezone.utc)


# ===========================================================================
# Notification tests
# ===========================================================================
class TestNotify:
    @patch("notifications.apprise.Apprise")
    def test_sends_notification(self, mock_apprise_cls):
        db.set_setting("apprise_urls", "json://localhost/test")
        mock_ap = MagicMock()
        mock_apprise_cls.return_value = mock_ap

        notifications.notify_new_results("TestQuery", "ubuntu", SAMPLE_RESULTS)

        mock_ap.add.assert_called_once_with("json://localhost/test")
        mock_ap.notify.assert_called_once()
        title = mock_ap.notify.call_args.kwargs["title"]
        assert "2 new results" in title
        assert "TestQuery" in title

    @patch("notifications.apprise.Apprise")
    def test_skips_when_no_urls(self, mock_apprise_cls):
        db.set_setting("apprise_urls", "")
        notifications.notify_new_results("Test", "q", [{"title": "t"}])
        mock_apprise_cls.return_value.notify.assert_not_called()

    @patch("notifications.apprise.Apprise")
    def test_plural_single_result(self, mock_apprise_cls):
        db.set_setting("apprise_urls", "json://localhost/test")
        mock_ap = MagicMock()
        mock_apprise_cls.return_value = mock_ap

        notifications.notify_new_results("Q", "q", [SAMPLE_RESULTS[0]])

        title = mock_ap.notify.call_args.kwargs["title"]
        assert "1 new result " in title  # no trailing 's'

    @patch("notifications.apprise.Apprise")
    def test_truncates_at_20(self, mock_apprise_cls):
        db.set_setting("apprise_urls", "json://localhost/test")
        mock_ap = MagicMock()
        mock_apprise_cls.return_value = mock_ap

        items = [{"title": f"item-{i}", "indexer": "X", "size": 100} for i in range(25)]
        notifications.notify_new_results("Q", "q", items)

        body = mock_ap.notify.call_args.kwargs["body"]
        assert "… and 5 more" in body


# ===========================================================================
# Route tests — pages
# ===========================================================================
class TestIndexPage:
    def test_get_empty(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"No queries yet" in resp.data

    def test_get_with_queries(self, client):
        _insert_query(name="Ubuntu Watch")
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Ubuntu Watch" in resp.data


class TestSettingsPage:
    def test_get(self, client):
        resp = client.get("/settings")
        assert resp.status_code == 200
        assert b"Prowlarr" in resp.data

    def test_post_saves_settings(self, client):
        resp = client.post(
            "/settings",
            data={
                "prowlarr_url": "http://new-host:9696",
                "prowlarr_api_key": "newkey",
                "default_cron": "*/10 * * * *",
                "min_query_interval": "5",
                "apprise_urls": "json://localhost",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert db.get_setting("prowlarr_url") == "http://new-host:9696"
        assert db.get_setting("prowlarr_api_key") == "newkey"
        assert db.get_setting("default_cron") == "*/10 * * * *"
        assert db.get_setting("min_query_interval") == "5"
        assert db.get_setting("apprise_urls") == "json://localhost"

    def test_saved_flash(self, client):
        resp = client.post(
            "/settings",
            data={
                "prowlarr_url": "http://x",
                "prowlarr_api_key": "k",
                "default_cron": "0 * * * *",
                "min_query_interval": "10",
                "apprise_urls": "",
            },
            follow_redirects=True,
        )
        assert b"Settings saved" in resp.data


class TestQueryDetailPage:
    def test_get_existing(self, client):
        qid = _insert_query(name="Detail Test")
        _insert_result(qid, title="Result One")
        resp = client.get(f"/query/{qid}")
        assert resp.status_code == 200
        assert b"Detail Test" in resp.data
        assert b"Result One" in resp.data

    def test_get_nonexistent(self, client):
        resp = client.get("/query/9999")
        assert resp.status_code == 404


# ===========================================================================
# Route tests — API actions
# ===========================================================================
class TestSearchPreview:
    @patch.object(worker.work_queue, "submit")
    def test_empty_query(self, mock_submit, client):
        resp = client.post("/api/search-preview", data={"query": ""})
        assert b"Enter a query above" in resp.data
        mock_submit.assert_not_called()

    @patch.object(worker.work_queue, "submit")
    def test_submits_job_and_returns_polling_div(self, mock_submit, client):
        job = worker.Job(job_id="abc123")
        mock_submit.return_value = job

        resp = client.post("/api/search-preview", data={"query": "ubuntu"})
        assert resp.status_code == 200
        assert b"abc123" in resp.data
        assert b"hx-get" in resp.data
        assert b"every 1s" in resp.data
        mock_submit.assert_called_once()


class TestJobPreview:
    @patch.object(worker.work_queue, "get_job")
    def test_not_found(self, mock_get, client):
        mock_get.return_value = None
        resp = client.get("/api/job/bad-id/preview")
        assert b"expired or not found" in resp.data

    @patch.object(worker.work_queue, "get_job")
    def test_queued_state(self, mock_get, client):
        job = worker.Job(job_id="q1", status="queued")
        mock_get.return_value = job
        resp = client.get("/api/job/q1/preview")
        assert b"queued" in resp.data
        assert b"hx-get" in resp.data

    @patch.object(worker.work_queue, "get_job")
    def test_running_state(self, mock_get, client):
        job = worker.Job(job_id="r1", status="running")
        mock_get.return_value = job
        resp = client.get("/api/job/r1/preview")
        assert b"searching" in resp.data
        assert b"hx-get" in resp.data

    @patch.object(worker.work_queue, "get_job")
    def test_error_state(self, mock_get, client):
        job = worker.Job(job_id="e1", status="error", error="timeout")
        mock_get.return_value = job
        resp = client.get("/api/job/e1/preview")
        assert b"Search failed" in resp.data
        assert b"timeout" in resp.data

    @patch.object(worker.work_queue, "get_job")
    def test_done_returns_results(self, mock_get, client):
        job = worker.Job(job_id="d1", status="done", result=SAMPLE_RESULTS)
        mock_get.return_value = job
        resp = client.get("/api/job/d1/preview")
        assert b"Ubuntu 24.04" in resp.data
        # Should NOT contain polling trigger
        assert b"hx-trigger" not in resp.data


class TestAddQuery:
    @patch.object(worker.work_queue, "submit")
    def test_creates_query_and_submits_seed(self, mock_submit, client):
        mock_submit.return_value = worker.Job()
        resp = client.post(
            "/api/query",
            data={"name": "New Watch", "query": "fedora", "cron": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 302

        with db.get_db() as conn:
            q = conn.execute("SELECT * FROM queries WHERE name='New Watch'").fetchone()
        assert q is not None
        assert q["query"] == "fedora"
        assert q["next_run"] is not None

        mock_submit.assert_called_once()
        call_kwargs = mock_submit.call_args.kwargs
        assert call_kwargs["label"].startswith("seed:")
        assert call_kwargs["priority"] == worker.Priority.HIGH

    @patch.object(worker.work_queue, "submit")
    def test_custom_cron(self, mock_submit, client):
        mock_submit.return_value = worker.Job()
        client.post(
            "/api/query",
            data={"name": "Cron Test", "query": "test", "cron": "*/5 * * * *"},
        )

        with db.get_db() as conn:
            q = conn.execute("SELECT cron FROM queries WHERE name='Cron Test'").fetchone()
        assert q["cron"] == "*/5 * * * *"

    def test_missing_fields(self, client):
        resp = client.post("/api/query", data={"name": "", "query": ""})
        assert resp.status_code == 400


class TestUpdateQuery:
    def test_delete(self, client):
        qid = _insert_query(name="To Delete")
        resp = client.post(f"/api/query/{qid}", data={"action": "delete"}, follow_redirects=False)
        assert resp.status_code == 302

        with db.get_db() as conn:
            q = conn.execute("SELECT * FROM queries WHERE id=?", (qid,)).fetchone()
        assert q is None

    def test_toggle_disable(self, client):
        qid = _insert_query(enabled=1)
        client.post(f"/api/query/{qid}", data={"action": "toggle"})

        with db.get_db() as conn:
            q = conn.execute("SELECT enabled FROM queries WHERE id=?", (qid,)).fetchone()
        assert q["enabled"] == 0

    def test_toggle_enable(self, client):
        qid = _insert_query(enabled=0)
        client.post(f"/api/query/{qid}", data={"action": "toggle"})

        with db.get_db() as conn:
            q = conn.execute("SELECT enabled FROM queries WHERE id=?", (qid,)).fetchone()
        assert q["enabled"] == 1

    @patch.object(worker.work_queue, "submit")
    def test_run_now(self, mock_submit, client):
        mock_submit.return_value = worker.Job()
        qid = _insert_query(query="my-search")
        resp = client.post(f"/api/query/{qid}", data={"action": "run_now"}, follow_redirects=False)
        assert resp.status_code == 302

        mock_submit.assert_called_once()
        call_kwargs = mock_submit.call_args.kwargs
        assert call_kwargs["priority"] == worker.Priority.HIGH
        assert call_kwargs["label"] == f"run:{qid}"

    def test_update_cron(self, client):
        qid = _insert_query()
        client.post(f"/api/query/{qid}", data={"action": "update_cron", "cron": "*/15 * * * *"})

        with db.get_db() as conn:
            q = conn.execute("SELECT cron, next_run FROM queries WHERE id=?", (qid,)).fetchone()
        assert q["cron"] == "*/15 * * * *"
        assert q["next_run"] is not None

    def test_unknown_action(self, client):
        qid = _insert_query()
        resp = client.post(f"/api/query/{qid}", data={"action": "bogus"})
        assert resp.status_code == 400


class TestQueueStatus:
    @patch.object(worker.work_queue, "status")
    def test_empty(self, mock_status, client):
        mock_status.return_value = {"queued": set(), "running": None}
        resp = client.get("/api/queue-status")
        data = resp.get_json()
        assert data["queries"] == {}
        assert data["preview"] is None

    @patch.object(worker.work_queue, "status")
    def test_with_queued_queries(self, mock_status, client):
        mock_status.return_value = {"queued": {"q:1", "q:2"}, "running": None}
        resp = client.get("/api/queue-status")
        data = resp.get_json()
        assert data["queries"]["1"] == "queued"
        assert data["queries"]["2"] == "queued"

    @patch.object(worker.work_queue, "status")
    def test_with_running_query(self, mock_status, client):
        mock_status.return_value = {"queued": set(), "running": "q:5"}
        resp = client.get("/api/queue-status")
        data = resp.get_json()
        assert data["queries"]["5"] == "running"

    @patch.object(worker.work_queue, "status")
    def test_preview_queued(self, mock_status, client):
        mock_status.return_value = {"queued": {"preview:abc123"}, "running": None}
        data = client.get("/api/queue-status").get_json()
        assert data["preview"] == "queued"

    @patch.object(worker.work_queue, "status")
    def test_preview_running(self, mock_status, client):
        mock_status.return_value = {"queued": set(), "running": "preview:abc123"}
        data = client.get("/api/queue-status").get_json()
        assert data["preview"] == "running"


class TestTestProwlarr:
    @patch("routes.requests.get")
    def test_success(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"version": "1.2.3"}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        resp = client.post(
            "/api/test-prowlarr",
            data={"prowlarr_url": "http://localhost:9696", "prowlarr_api_key": "key"},
        )
        data = resp.get_json()
        assert data["ok"] is True
        assert "1.2.3" in data["message"]

    def test_missing_fields(self, client):
        resp = client.post("/api/test-prowlarr", data={"prowlarr_url": "", "prowlarr_api_key": ""})
        data = resp.get_json()
        assert data["ok"] is False

    @patch("routes.requests.get")
    def test_connection_error(self, mock_get, client):
        mock_get.side_effect = __import__("requests").exceptions.ConnectionError()
        resp = client.post(
            "/api/test-prowlarr",
            data={"prowlarr_url": "http://x", "prowlarr_api_key": "k"},
        )
        data = resp.get_json()
        assert data["ok"] is False
        assert "Connection refused" in data["message"]

    @patch("routes.requests.get")
    def test_timeout(self, mock_get, client):
        mock_get.side_effect = __import__("requests").exceptions.Timeout()
        resp = client.post(
            "/api/test-prowlarr",
            data={"prowlarr_url": "http://x", "prowlarr_api_key": "k"},
        )
        data = resp.get_json()
        assert data["ok"] is False
        assert "timed out" in data["message"]

    @patch("routes.requests.get")
    def test_unauthorized(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.raise_for_status.side_effect = __import__("requests").exceptions.HTTPError(
            response=mock_resp
        )
        mock_get.return_value = mock_resp
        resp = client.post(
            "/api/test-prowlarr",
            data={"prowlarr_url": "http://x", "prowlarr_api_key": "k"},
        )
        data = resp.get_json()
        assert data["ok"] is False
        assert "Unauthorized" in data["message"]


class TestTestApprise:
    @patch("routes.apprise.Apprise")
    def test_no_urls(self, mock_cls, client):
        resp = client.post("/api/test-apprise", data={"apprise_urls": ""})
        data = resp.get_json()
        assert data["ok"] is False

    @patch("routes.apprise.Apprise")
    def test_success(self, mock_cls, client):
        mock_ap = MagicMock()
        mock_ap.notify.return_value = True
        mock_cls.return_value = mock_ap

        resp = client.post("/api/test-apprise", data={"apprise_urls": "json://localhost"})
        data = resp.get_json()
        assert data["ok"] is True
        assert "Sent" in data["message"]
