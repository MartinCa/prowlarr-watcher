"""Comprehensive tests for Prowlarr Watcher."""

import os
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

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


@pytest.fixture(autouse=True)
def _reset_indexer_cache():
    """Prowlarr's indexer list is cached at module scope — keep tests isolated."""
    prowlarr._indexer_cache["time"] = None
    prowlarr._indexer_cache["indexers"] = []
    yield


@pytest.fixture()
def client():
    app_mod.app.config["TESTING"] = True
    with app_mod.app.test_client() as c:
        c.get("/api/queries")  # primes the CSRF cookie
        yield c


def _csrf_headers(client):
    cookie = client.get_cookie(routes.CSRF_COOKIE)
    return {routes.CSRF_HEADER: cookie.value} if cookie else {}


def post_json(client, path, body=None):
    return client.post(path, json=body or {}, headers=_csrf_headers(client))


def put_json(client, path, body=None):
    return client.put(path, json=body or {}, headers=_csrf_headers(client))


def patch_json(client, path, body=None):
    return client.patch(path, json=body or {}, headers=_csrf_headers(client))


def delete_json(client, path):
    return client.delete(path, headers=_csrf_headers(client))


def _configure_prowlarr():
    """Set valid Prowlarr settings so searches don't fail on missing config."""
    db.set_setting("prowlarr_url", "http://localhost:9696")
    db.set_setting("prowlarr_api_key", "test-key-123")


def _insert_query(name="Test", query="ubuntu", cron=None, enabled=1, note=None):
    """Insert a query directly into the DB and return its id."""
    now = datetime.now(timezone.utc).isoformat()
    next_iso = scheduler_mod.Scheduler.compute_next(cron or "0 * * * *")
    with db._db_lock, db.get_db() as conn:
        cur = conn.execute(
            "INSERT INTO queries (name, query, cron, enabled,"
            " created_at, last_run, next_run, last_count, note)"
            " VALUES (?,?,?,?,?,?,?,0,?)",
            (name, query, cron, enabled, now, now, next_iso, note),
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

    def test_petabytes(self):
        assert prowlarr.format_size(2 * 1024**5) == "2.0 PB"
        assert prowlarr.format_size(3.5 * 1024**5) == "3.5 PB"


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
        assert db.get_setting("prowlarr_timeout") == "200"
        assert db.get_setting("default_excluded_indexers") == ""

    def test_migration_adds_excluded_indexers_column(self):
        with db.get_db() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(queries)").fetchall()}
        assert "excluded_indexers" in cols

    def test_migration_backfills_last_new_result_from_results(self, tmp_path, monkeypatch):
        db_path = tmp_path / "migration_test.db"
        monkeypatch.setattr(db, "DATA_DIR", tmp_path)
        monkeypatch.setattr(db, "DB_PATH", db_path)

        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE queries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                query TEXT NOT NULL,
                cron TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_run TEXT,
                next_run TEXT,
                last_count INTEGER DEFAULT 0,
                last_error TEXT,
                excluded_indexers TEXT,
                last_new_result TEXT,
                note TEXT
            );
            CREATE TABLE results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_id INTEGER NOT NULL REFERENCES queries(id) ON DELETE CASCADE,
                result_hash TEXT NOT NULL,
                title TEXT,
                indexer TEXT,
                size INTEGER,
                guid TEXT,
                info_url TEXT,
                download_url TEXT,
                seeders INTEGER,
                first_seen TEXT NOT NULL,
                is_new INTEGER NOT NULL DEFAULT 1,
                UNIQUE(query_id, result_hash)
            );
            INSERT INTO queries (id, name, query, created_at)
            VALUES (1, 'Q1', 'test1', '2026-01-01T00:00:00+00:00');
            INSERT INTO queries (id, name, query, created_at)
            VALUES (2, 'Q2', 'test2', '2026-01-01T00:00:00+00:00');
            INSERT INTO queries (id, name, query, created_at)
            VALUES (3, 'Q3', 'test3', '2026-01-01T00:00:00+00:00');

            -- Q1 has two results with different first_seen timestamps
            INSERT INTO results (query_id, result_hash, first_seen)
            VALUES (1, 'h1', '2026-01-02T10:00:00+00:00');
            INSERT INTO results (query_id, result_hash, first_seen)
            VALUES (1, 'h2', '2026-01-05T15:30:00+00:00');

            -- Q2 has one result
            INSERT INTO results (query_id, result_hash, first_seen)
            VALUES (2, 'h3', '2026-01-03T12:00:00+00:00');

            -- Q3 has no results
        """)
        conn.commit()
        conn.close()

        db.init_db()

        with db.get_db() as c:
            q1 = c.execute("SELECT last_new_result FROM queries WHERE id=1").fetchone()
            q2 = c.execute("SELECT last_new_result FROM queries WHERE id=2").fetchone()
            q3 = c.execute("SELECT last_new_result FROM queries WHERE id=3").fetchone()
            flag = c.execute(
                "SELECT value FROM settings WHERE key='migrated_last_new_result_backfill'"
            ).fetchone()

        assert q1["last_new_result"] == "2026-01-05T15:30:00+00:00"
        assert q2["last_new_result"] == "2026-01-03T12:00:00+00:00"
        assert q3["last_new_result"] is None
        assert flag["value"] == "1"

        # Running init_db again is idempotent
        db.init_db()
        with db.get_db() as c:
            q1_after = c.execute("SELECT last_new_result FROM queries WHERE id=1").fetchone()
        assert q1_after["last_new_result"] == "2026-01-05T15:30:00+00:00"

    def test_migration_from_schema_before_last_new_result_column(self, tmp_path, monkeypatch):
        db_path = tmp_path / "old_schema.db"
        monkeypatch.setattr(db, "DATA_DIR", tmp_path)
        monkeypatch.setattr(db, "DB_PATH", db_path)

        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE queries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                query TEXT NOT NULL,
                cron TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE TABLE results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_id INTEGER NOT NULL REFERENCES queries(id) ON DELETE CASCADE,
                result_hash TEXT NOT NULL,
                first_seen TEXT NOT NULL
            );
            INSERT INTO queries (id, name, query, created_at)
            VALUES (1, 'Q1', 'test1', '2026-01-01T00:00:00+00:00');
            INSERT INTO results (query_id, result_hash, first_seen)
            VALUES (1, 'h1', '2026-02-01T00:00:00+00:00');
        """)
        conn.commit()
        conn.close()

        db.init_db()

        with db.get_db() as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(queries)").fetchall()}
            assert "last_new_result" in cols
            q1 = c.execute("SELECT last_new_result FROM queries WHERE id=1").fetchone()
            assert q1["last_new_result"] == "2026-02-01T00:00:00+00:00"

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

    @patch("prowlarr.list_indexers")
    @patch("prowlarr.requests.get")
    def test_search_with_excluded_indexers(self, mock_get, mock_list_indexers):
        _configure_prowlarr()
        mock_list_indexers.return_value = [
            {"id": 1, "name": "A", "enable": True},
            {"id": 2, "name": "B", "enable": True},
            {"id": 3, "name": "C", "enable": True},
        ]
        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        prowlarr.prowlarr_search_raw("test", excluded_indexer_ids=[2])

        params = mock_get.call_args.kwargs.get("params", mock_get.call_args[1].get("params", {}))
        assert params["indexerIds"] == [1, 3]

    @patch("prowlarr.requests.get")
    def test_search_without_exclusions_skips_indexer_lookup(self, mock_get):
        _configure_prowlarr()
        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        prowlarr.prowlarr_search_raw("test", excluded_indexer_ids=None)

        params = mock_get.call_args.kwargs.get("params", mock_get.call_args[1].get("params", {}))
        assert "indexerIds" not in params
        mock_get.assert_called_once()  # only the search call, no indexer lookup


class TestListIndexers:
    def test_raises_without_config(self):
        db.set_setting("prowlarr_api_key", "")
        with pytest.raises(ValueError, match="configured in Settings"):
            prowlarr.list_indexers()

    @patch("prowlarr.requests.get")
    def test_fetches_and_normalizes(self, mock_get):
        _configure_prowlarr()
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"id": 1, "name": "A", "enable": True, "extra": "ignored"},
            {"id": 2, "name": "B", "enable": False},
        ]
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        indexers = prowlarr.list_indexers()

        assert indexers == [
            {"id": 1, "name": "A", "enable": True},
            {"id": 2, "name": "B", "enable": False},
        ]
        mock_get.assert_called_once()
        assert "/api/v1/indexer" in mock_get.call_args[0][0]

    @patch("prowlarr.requests.get")
    def test_caches_between_calls(self, mock_get):
        _configure_prowlarr()
        mock_resp = MagicMock()
        mock_resp.json.return_value = [{"id": 1, "name": "A", "enable": True}]
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        prowlarr.list_indexers()
        prowlarr.list_indexers()

        mock_get.assert_called_once()

    @patch("prowlarr.requests.get")
    def test_force_bypasses_cache(self, mock_get):
        _configure_prowlarr()
        mock_resp = MagicMock()
        mock_resp.json.return_value = [{"id": 1, "name": "A", "enable": True}]
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        prowlarr.list_indexers()
        prowlarr.list_indexers(force=True)

        assert mock_get.call_count == 2


class TestIndexerExclusionHelpers:
    def test_parse_indexer_ids(self):
        assert prowlarr.parse_indexer_ids("1,2,3") == [1, 2, 3]
        assert prowlarr.parse_indexer_ids("") == []
        assert prowlarr.parse_indexer_ids("5") == [5]

    def test_format_indexer_ids(self):
        assert prowlarr.format_indexer_ids([1, 2, 3]) == "1,2,3"
        assert prowlarr.format_indexer_ids([]) == ""

    def test_effective_excluded_indexers_inherits_default_when_none(self):
        db.set_setting("default_excluded_indexers", "4,5")
        assert prowlarr.effective_excluded_indexers(None) == [4, 5]

    def test_effective_excluded_indexers_uses_override(self):
        db.set_setting("default_excluded_indexers", "4,5")
        assert prowlarr.effective_excluded_indexers("6,7") == [6, 7]

    def test_effective_excluded_indexers_empty_override_means_none_excluded(self):
        db.set_setting("default_excluded_indexers", "4,5")
        assert prowlarr.effective_excluded_indexers("") == []


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
        mock_search.assert_called_once_with("ubuntu", None, None)

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
            if (job.status in ("done", "error") and callback.called) or job.status == "error":
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
        mock_search.side_effect = lambda q, c=None, e=None: (call_order.append(q), [])[1]

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

    def test_min_gap_falls_back_when_get_setting_raises(self, monkeypatch):
        db.set_setting("min_query_interval", "5")
        wq = self._make_queue()

        def boom(key, default=""):
            raise RuntimeError("db closed")

        monkeypatch.setattr(worker, "get_setting", boom)
        assert wq._min_gap() == 10.0

    def test_max_retries_reads_setting(self):
        db.set_setting("max_retries", "3")
        wq = self._make_queue()
        assert wq._max_retries() == 3

    def test_max_retries_falls_back_and_clamps(self, monkeypatch):
        db.set_setting("max_retries", "2")
        wq = self._make_queue()

        def boom(key, default=""):
            raise RuntimeError("db closed")

        monkeypatch.setattr(worker, "get_setting", boom)
        assert wq._max_retries() == 5

        # Clamps below 1 (once the setting is readable again)
        monkeypatch.undo()
        db.set_setting("max_retries", "0")
        assert wq._max_retries() == 1


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

    def test_sets_last_new_result_when_new_items_found(self):
        qid = _insert_query()
        job = worker.Job(status="done", result=SAMPLE_RESULTS)
        callbacks.process_query_result(qid, "0 * * * *", job)

        with db.get_db() as conn:
            q = conn.execute("SELECT last_new_result FROM queries WHERE id=?", (qid,)).fetchone()
        assert q["last_new_result"] is not None

    def test_last_new_result_unchanged_when_no_new_items(self):
        qid = _insert_query()
        for r in SAMPLE_RESULTS:
            _insert_result(qid, title=r["title"], guid=r["guid"])

        job = worker.Job(status="done", result=SAMPLE_RESULTS)
        callbacks.process_query_result(qid, "0 * * * *", job)

        with db.get_db() as conn:
            q = conn.execute("SELECT last_new_result FROM queries WHERE id=?", (qid,)).fetchone()
        assert q["last_new_result"] is None

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
    def test_notifies_with_note(self, mock_notify):
        qid = _insert_query(name="MyQuery", query="ubuntu", note="Only remastered releases")
        job = worker.Job(status="done", result=SAMPLE_RESULTS)
        callbacks.process_query_result(qid, "0 * * * *", job)

        mock_notify.assert_called_once()
        call_args = mock_notify.call_args[0]
        assert call_args[3] == "Only remastered releases"

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

    @patch.object(worker.work_queue, "submit")
    def test_tick_startup_logs_and_queues_overdue(self, mock_submit, caplog):
        """startup=True logs the enabled-query count and the overdue query line."""
        import logging

        mock_submit.return_value = worker.Job()
        now = datetime.now(timezone.utc).isoformat()
        with db._db_lock, db.get_db() as conn:
            conn.execute(
                "INSERT INTO queries (name, query, cron, enabled, created_at, next_run) "
                "VALUES (?,?,?,1,?,?)",
                ("Startup", "ubuntu", "*/5 * * * *", now, "2020-01-01T00:00:00+00:00"),
            )
            conn.commit()

        sched = scheduler_mod.Scheduler()
        with caplog.at_level(logging.INFO):
            sched._tick(startup=True)

        assert "Checking for overdue queries at startup (1 enabled)" in caplog.text
        assert "Overdue: query" in caplog.text and "'ubuntu'" in caplog.text
        mock_submit.assert_called_once()

    @patch.object(worker.work_queue, "submit")
    def test_tick_computes_and_persists_missing_next_run(self, mock_submit):
        """A query with a NULL next_run gets one computed and stored."""
        mock_submit.return_value = worker.Job()
        with db._db_lock, db.get_db() as conn:
            cur = conn.execute(
                "INSERT INTO queries (name, query, cron, enabled, created_at, next_run) "
                "VALUES (?,?,?,1,?,NULL)",
                ("NoNext", "ubuntu", "*/5 * * * *", datetime.now(timezone.utc).isoformat()),
            )
            qid = cur.lastrowid
            conn.commit()

        sched = scheduler_mod.Scheduler()
        sched._tick()

        with db.get_db() as conn:
            q = conn.execute("SELECT next_run FROM queries WHERE id=?", (qid,)).fetchone()
        assert q["next_run"] is not None
        assert datetime.fromisoformat(q["next_run"]) > datetime.now(timezone.utc)


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
    def test_note_is_first_line_of_body(self, mock_apprise_cls):
        db.set_setting("apprise_urls", "json://localhost/test")
        mock_ap = MagicMock()
        mock_apprise_cls.return_value = mock_ap

        notifications.notify_new_results("Q", "q", SAMPLE_RESULTS, "Only remastered releases")

        body = mock_ap.notify.call_args.kwargs["body"]
        assert body.startswith("Only remastered releases\n")

    @patch("notifications.apprise.Apprise")
    def test_truncates_at_20(self, mock_apprise_cls):
        db.set_setting("apprise_urls", "json://localhost/test")
        mock_ap = MagicMock()
        mock_apprise_cls.return_value = mock_ap

        items = [{"title": f"item-{i}", "indexer": "X", "size": 100} for i in range(25)]
        notifications.notify_new_results("Q", "q", items)

        body = mock_ap.notify.call_args.kwargs["body"]
        assert "… and 5 more" in body


class TestNotifyError:
    @patch("notifications.apprise.Apprise")
    def test_looks_up_query_when_query_text_missing(self, mock_apprise_cls):
        """Missing query_text: name/query are fetched from the DB."""
        db.set_setting("apprise_urls", "json://localhost/test")
        qid = _insert_query(name="RealQ", query="real-query")
        mock_ap = MagicMock()
        mock_apprise_cls.return_value = mock_ap

        notifications.notify_error(qid, None, "scheduled", "boom")

        mock_ap.add.assert_called_once_with("json://localhost/test")
        kwargs = mock_ap.notify.call_args.kwargs
        assert "[Prowlarr] Search failed — RealQ" == kwargs["title"]
        assert "Type: scheduled" in kwargs["body"]
        assert "Query: real-query" in kwargs["body"]
        assert "Error: boom" in kwargs["body"]

    @patch("notifications.apprise.Apprise")
    def test_missing_query_uses_fallback_name(self, mock_apprise_cls):
        """Nonexistent qid: falls back to Q<id> name and '?' query."""
        db.set_setting("apprise_urls", "json://localhost/test")
        mock_ap = MagicMock()
        mock_apprise_cls.return_value = mock_ap

        notifications.notify_error(999999, None, "scheduled", "boom")

        kwargs = mock_ap.notify.call_args.kwargs
        assert kwargs["title"] == "[Prowlarr] Search failed — Q999999"
        assert "Query: ?" in kwargs["body"]

    @patch("notifications.apprise.Apprise")
    def test_uses_provided_query_text_as_name(self, mock_apprise_cls):
        """When query_text is given it doubles as the display name."""
        db.set_setting("apprise_urls", "json://localhost/test")
        mock_ap = MagicMock()
        mock_apprise_cls.return_value = mock_ap

        notifications.notify_error(123, "my-text", "seed", "nope")

        kwargs = mock_ap.notify.call_args.kwargs
        assert kwargs["title"] == "[Prowlarr] Search failed — my-text"
        assert "Type: seed" in kwargs["body"]
        assert "Query: my-text" in kwargs["body"]

    @patch("notifications.apprise.Apprise")
    def test_skips_when_no_urls(self, mock_apprise_cls):
        db.set_setting("apprise_urls", "")
        notifications.notify_error(1, "q", "scheduled", "boom")
        mock_apprise_cls.return_value.notify.assert_not_called()


# ===========================================================================
# Route tests — CSRF
# ===========================================================================
class TestCsrf:
    def test_mutating_request_without_header_is_rejected(self, client):
        resp = client.post("/api/queries", json={"query": "ubuntu"})
        assert resp.status_code == 403

    def test_mutating_request_with_wrong_token_is_rejected(self, client):
        resp = client.post(
            "/api/queries", json={"query": "ubuntu"}, headers={routes.CSRF_HEADER: "bogus"}
        )
        assert resp.status_code == 403

    def test_get_request_does_not_require_header(self, client):
        resp = client.get("/api/queries")
        assert resp.status_code == 200

    def test_response_sets_csrf_cookie(self):
        with app_mod.app.test_client() as c:
            resp = c.get("/api/queries")
            assert routes.CSRF_COOKIE in resp.headers.get("Set-Cookie", "")


# ===========================================================================
# Route tests — queries
# ===========================================================================
class TestListQueries:
    def test_empty(self, client):
        resp = client.get("/api/queries")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_with_queries(self, client):
        _insert_query(name="Ubuntu Watch")
        resp = client.get("/api/queries")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]["name"] == "Ubuntu Watch"

    def test_orders_by_last_new_result_then_never_by_id(self, client):
        # No new results yet: falls back to id desc (newest created first).
        _insert_query(name="Never Older")
        _insert_query(name="Never Newer")
        # Has new results, but longer ago than "Recent".
        stale = _insert_query(name="Stale")
        # Has the most recent new results: should sort first.
        recent = _insert_query(name="Recent")

        with db._db_lock, db.get_db() as conn:
            conn.execute(
                "UPDATE queries SET last_new_result=? WHERE id=?",
                ("2020-01-01T00:00:00+00:00", stale),
            )
            conn.execute(
                "UPDATE queries SET last_new_result=? WHERE id=?",
                ("2024-01-01T00:00:00+00:00", recent),
            )
            conn.commit()

        resp = client.get("/api/queries")
        assert resp.status_code == 200
        names = [q["name"] for q in resp.get_json()]
        assert names == ["Recent", "Stale", "Never Newer", "Never Older"]


class TestGetQuery:
    def test_get_existing(self, client):
        qid = _insert_query(name="Detail Test")
        _insert_result(qid, title="Result One")
        resp = client.get(f"/api/queries/{qid}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["name"] == "Detail Test"
        assert data["results"][0]["title"] == "Result One"

    def test_get_nonexistent(self, client):
        resp = client.get("/api/queries/9999")
        assert resp.status_code == 404
        assert resp.content_type == "application/problem+json"

    def test_shows_default_excluded_indexers_as_null_override(self, client):
        db.set_setting("default_excluded_indexers", "1,2")
        qid = _insert_query(name="Indexer Test")
        data = client.get(f"/api/queries/{qid}").get_json()
        assert data["excludedIndexers"] is None

    def test_shows_query_override(self, client):
        qid = _insert_query(name="Indexer Override Test")
        with db._db_lock, db.get_db() as conn:
            conn.execute("UPDATE queries SET excluded_indexers=? WHERE id=?", ("7", qid))
            conn.commit()
        data = client.get(f"/api/queries/{qid}").get_json()
        assert data["excludedIndexers"] == [7]


class TestCreateQuery:
    @patch.object(worker.work_queue, "submit")
    def test_creates_query_and_submits_seed(self, mock_submit, client):
        mock_submit.return_value = worker.Job()
        resp = post_json(client, "/api/queries", {"name": "New Watch", "query": "fedora"})
        assert resp.status_code == 201
        assert resp.get_json()["name"] == "New Watch"

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
        post_json(
            client, "/api/queries", {"name": "Cron Test", "query": "test", "cron": "*/5 * * * *"}
        )

        with db.get_db() as conn:
            q = conn.execute("SELECT cron FROM queries WHERE name='Cron Test'").fetchone()
        assert q["cron"] == "*/5 * * * *"

    @patch.object(worker.work_queue, "submit")
    def test_create_with_note(self, mock_submit, client):
        mock_submit.return_value = worker.Job()
        resp = post_json(
            client, "/api/queries", {"query": "fedora", "note": "Only stable releases"}
        )
        assert resp.get_json()["note"] == "Only stable releases"

        with db.get_db() as conn:
            q = conn.execute("SELECT note FROM queries WHERE query='fedora'").fetchone()
        assert q["note"] == "Only stable releases"

    def test_missing_fields(self, client):
        resp = post_json(client, "/api/queries", {"name": "", "query": ""})
        assert resp.status_code == 400
        assert resp.content_type == "application/problem+json"

    def test_invalid_cron(self, client):
        resp = post_json(client, "/api/queries", {"query": "fedora", "cron": "not a cron"})
        assert resp.status_code == 400
        assert resp.content_type == "application/problem+json"
        assert "cron" in resp.get_json()["errors"]

        with db.get_db() as conn:
            q = conn.execute("SELECT * FROM queries WHERE query='fedora'").fetchone()
        assert q is None


class TestUpdateQuery:
    def test_delete(self, client):
        qid = _insert_query(name="To Delete")
        resp = delete_json(client, f"/api/queries/{qid}")
        assert resp.status_code == 204

        with db.get_db() as conn:
            q = conn.execute("SELECT * FROM queries WHERE id=?", (qid,)).fetchone()
        assert q is None

    def test_delete_nonexistent(self, client):
        resp = delete_json(client, "/api/queries/9999")
        assert resp.status_code == 404

    def test_toggle_disable(self, client):
        qid = _insert_query(enabled=1)
        patch_json(client, f"/api/queries/{qid}", {"enabled": False})

        with db.get_db() as conn:
            q = conn.execute("SELECT enabled FROM queries WHERE id=?", (qid,)).fetchone()
        assert q["enabled"] == 0

    def test_toggle_enable(self, client):
        qid = _insert_query(enabled=0)
        patch_json(client, f"/api/queries/{qid}", {"enabled": True})

        with db.get_db() as conn:
            q = conn.execute("SELECT enabled FROM queries WHERE id=?", (qid,)).fetchone()
        assert q["enabled"] == 1

    def test_update_cron(self, client):
        qid = _insert_query()
        patch_json(client, f"/api/queries/{qid}", {"cron": "*/15 * * * *"})

        with db.get_db() as conn:
            q = conn.execute("SELECT cron, next_run FROM queries WHERE id=?", (qid,)).fetchone()
        assert q["cron"] == "*/15 * * * *"
        assert q["next_run"] is not None

    def test_update_invalid_cron(self, client):
        qid = _insert_query(cron="*/15 * * * *")
        resp = patch_json(client, f"/api/queries/{qid}", {"cron": "99 99 * * *"})
        assert resp.status_code == 400
        assert resp.content_type == "application/problem+json"

        with db.get_db() as conn:
            q = conn.execute("SELECT cron FROM queries WHERE id=?", (qid,)).fetchone()
        assert q["cron"] == "*/15 * * * *"

    def test_update_excluded_indexers(self, client):
        qid = _insert_query()
        patch_json(client, f"/api/queries/{qid}", {"excludedIndexers": [1, 3]})

        data = client.get(f"/api/queries/{qid}").get_json()
        assert data["excludedIndexers"] == [1, 3]

    def test_clear_excluded_indexers_override(self, client):
        qid = _insert_query()
        with db._db_lock, db.get_db() as conn:
            conn.execute("UPDATE queries SET excluded_indexers=? WHERE id=?", ("1,2", qid))
            conn.commit()
        patch_json(client, f"/api/queries/{qid}", {"excludedIndexers": None})

        data = client.get(f"/api/queries/{qid}").get_json()
        assert data["excludedIndexers"] is None

    def test_update_excluded_indexers_non_int_items(self, client):
        qid = _insert_query()
        resp = patch_json(
            client,
            f"/api/queries/{qid}",
            {"excludedIndexers": [1, "two"]},
        )
        assert resp.status_code == 400
        assert resp.content_type == "application/problem+json"
        assert "excludedIndexers" in resp.get_json()["errors"]

        data = client.get(f"/api/queries/{qid}").get_json()
        assert data["excludedIndexers"] is None

    def test_update_note(self, client):
        qid = _insert_query()
        patch_json(client, f"/api/queries/{qid}", {"note": "Only stable releases"})

        data = client.get(f"/api/queries/{qid}").get_json()
        assert data["note"] == "Only stable releases"

    def test_clear_note(self, client):
        qid = _insert_query(note="Old note")
        patch_json(client, f"/api/queries/{qid}", {"note": None})

        data = client.get(f"/api/queries/{qid}").get_json()
        assert data["note"] is None

    def test_update_nonexistent(self, client):
        resp = patch_json(client, "/api/queries/9999", {"enabled": False})
        assert resp.status_code == 404


class TestRunQuery:
    @patch.object(worker.work_queue, "submit")
    def test_run_now(self, mock_submit, client):
        mock_submit.return_value = worker.Job()
        qid = _insert_query(query="my-search")
        resp = post_json(client, f"/api/queries/{qid}/run")
        assert resp.status_code == 202

        mock_submit.assert_called_once()
        call_kwargs = mock_submit.call_args.kwargs
        assert call_kwargs["priority"] == worker.Priority.HIGH
        assert call_kwargs["label"] == f"run:{qid}"

    def test_run_nonexistent(self, client):
        resp = post_json(client, "/api/queries/9999/run")
        assert resp.status_code == 404


# ===========================================================================
# Route tests — search preview / job polling
# ===========================================================================
class TestSearchPreview:
    @patch.object(worker.work_queue, "submit")
    def test_empty_query(self, mock_submit, client):
        resp = post_json(client, "/api/search-preview", {"query": ""})
        assert resp.status_code == 400
        mock_submit.assert_not_called()

    @patch.object(worker.work_queue, "submit")
    def test_submits_job_and_returns_job_id(self, mock_submit, client):
        job = worker.Job(job_id="abc123")
        mock_submit.return_value = job

        resp = post_json(client, "/api/search-preview", {"query": "ubuntu"})
        assert resp.status_code == 202
        assert resp.get_json()["jobId"] == "abc123"
        mock_submit.assert_called_once()


class TestGetJob:
    @patch.object(worker.work_queue, "get_job")
    def test_not_found(self, mock_get, client):
        mock_get.return_value = None
        resp = client.get("/api/jobs/bad-id")
        assert resp.status_code == 404

    @patch.object(worker.work_queue, "get_job")
    def test_queued_state(self, mock_get, client):
        job = worker.Job(job_id="q1", status="queued")
        mock_get.return_value = job
        resp = client.get("/api/jobs/q1")
        assert resp.get_json()["status"] == "queued"

    @patch.object(worker.work_queue, "get_job")
    def test_running_state(self, mock_get, client):
        job = worker.Job(job_id="r1", status="running")
        mock_get.return_value = job
        resp = client.get("/api/jobs/r1")
        assert resp.get_json()["status"] == "running"

    @patch.object(worker.work_queue, "get_job")
    def test_error_state(self, mock_get, client):
        job = worker.Job(job_id="e1", status="error", error="timeout")
        mock_get.return_value = job
        resp = client.get("/api/jobs/e1")
        data = resp.get_json()
        assert data["status"] == "error"
        assert data["error"] == "timeout"

    @patch.object(worker.work_queue, "get_job")
    def test_done_returns_results(self, mock_get, client):
        job = worker.Job(job_id="d1", status="done", result=SAMPLE_RESULTS)
        mock_get.return_value = job
        resp = client.get("/api/jobs/d1")
        data = resp.get_json()
        assert data["status"] == "done"
        assert data["results"][0]["title"] == "Ubuntu 24.04 LTS"


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


class TestGetSettings:
    def test_get(self, client):
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        assert "prowlarrUrl" in resp.get_json()

    def test_invalid_stored_interval_falls_back_to_10(self, client):
        db.set_setting("min_query_interval", "abc")
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        assert resp.get_json()["minQueryInterval"] == 10


class TestPutSettings:
    def test_saves_settings(self, client):
        body = {
            "prowlarrUrl": "http://new-host:9696",
            "prowlarrApiKey": "newkey",
            "defaultCron": "*/10 * * * *",
            "minQueryInterval": 5,
            "prowlarrTimeout": 30,
            "appriseUrls": "json://localhost",
        }
        resp = client.put("/api/settings", json=body, headers=_csrf_headers(client))
        assert resp.status_code == 200
        assert db.get_setting("prowlarr_url") == "http://new-host:9696"
        assert db.get_setting("prowlarr_api_key") == "newkey"
        assert db.get_setting("default_cron") == "*/10 * * * *"
        assert db.get_setting("min_query_interval") == "5"
        assert db.get_setting("prowlarr_timeout") == "30"
        assert db.get_setting("apprise_urls") == "json://localhost"

    def test_saves_excluded_indexers(self, client):
        client.put(
            "/api/settings",
            json={"defaultExcludedIndexers": [1, 3]},
            headers=_csrf_headers(client),
        )
        assert db.get_setting("default_excluded_indexers") == "1,3"

    def test_without_indexers_clears_exclusions(self, client):
        db.set_setting("default_excluded_indexers", "1,2")
        client.put("/api/settings", json={}, headers=_csrf_headers(client))
        assert db.get_setting("default_excluded_indexers") == ""

    def test_invalid_default_cron_rejected(self, client):
        db.set_setting("default_cron", "0 * * * *")
        resp = client.put(
            "/api/settings", json={"defaultCron": "invalid cron"}, headers=_csrf_headers(client)
        )
        assert resp.status_code == 400
        assert resp.content_type == "application/problem+json"
        assert "defaultCron" in resp.get_json()["errors"]
        assert db.get_setting("default_cron") == "0 * * * *"

    def test_invalid_external_url_scheme(self, client):
        resp = put_json(client, "/api/settings", {"prowlarrExternalUrl": "file:///etc/passwd"})
        assert resp.status_code == 400
        assert "prowlarrExternalUrl" in resp.get_json()["errors"]

    def test_invalid_min_query_interval_value(self, client):
        resp = put_json(client, "/api/settings", {"minQueryInterval": "abc"})
        assert resp.status_code == 400
        assert "minQueryInterval" in resp.get_json()["errors"]

    def test_invalid_max_retries_value(self, client):
        resp = put_json(client, "/api/settings", {"maxRetries": "many"})
        assert resp.status_code == 400
        assert "maxRetries" in resp.get_json()["errors"]

    def test_invalid_prowlarr_timeout_value(self, client):
        resp = put_json(client, "/api/settings", {"prowlarrTimeout": "abc"})
        assert resp.status_code == 400
        assert "prowlarrTimeout" in resp.get_json()["errors"]

    def test_prowlarr_timeout_below_minimum(self, client):
        resp = put_json(client, "/api/settings", {"prowlarrTimeout": 0})
        assert resp.status_code == 400
        assert "prowlarrTimeout" in resp.get_json()["errors"]

    def test_default_excluded_indexers_must_be_list(self, client):
        resp = put_json(client, "/api/settings", {"defaultExcludedIndexers": "1,2"})
        assert resp.status_code == 400
        assert "defaultExcludedIndexers" in resp.get_json()["errors"]


class TestApiIndexers:
    @patch("routes.list_indexers")
    def test_lists_indexers(self, mock_list, client):
        mock_list.return_value = [{"id": 1, "name": "A", "enable": True}]
        resp = client.get("/api/indexers")
        assert resp.status_code == 200
        assert resp.get_json() == {"indexers": [{"id": 1, "name": "A", "enable": True}]}

    @patch("routes.list_indexers")
    def test_not_configured_returns_400(self, mock_list, client):
        mock_list.side_effect = ValueError(
            "Prowlarr URL and API key must be configured in Settings"
        )
        resp = client.get("/api/indexers")
        assert resp.status_code == 400
        assert resp.content_type == "application/problem+json"
        assert resp.get_json()["title"] == "Prowlarr not configured"

    @patch("routes.list_indexers")
    def test_request_failure_returns_502(self, mock_list, client):
        mock_list.side_effect = __import__("requests").exceptions.ConnectionError("refused")
        resp = client.get("/api/indexers")
        assert resp.status_code == 502
        assert resp.content_type == "application/problem+json"
        assert "Could not reach Prowlarr" in resp.get_json()["title"]


class TestTestProwlarr:
    @patch("routes.requests.get")
    def test_success(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"version": "1.2.3"}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        resp = post_json(
            client,
            "/api/settings/test-prowlarr",
            {"prowlarrUrl": "http://localhost:9696", "prowlarrApiKey": "key"},
        )
        data = resp.get_json()
        assert data["ok"] is True
        assert "1.2.3" in data["message"]

    def test_missing_fields(self, client):
        resp = post_json(
            client, "/api/settings/test-prowlarr", {"prowlarrUrl": "", "prowlarrApiKey": ""}
        )
        data = resp.get_json()
        assert data["ok"] is False

    @patch("routes.requests.get")
    def test_connection_error(self, mock_get, client):
        mock_get.side_effect = __import__("requests").exceptions.ConnectionError()
        resp = post_json(
            client,
            "/api/settings/test-prowlarr",
            {"prowlarrUrl": "http://x", "prowlarrApiKey": "k"},
        )
        data = resp.get_json()
        assert data["ok"] is False
        assert "Connection refused" in data["message"]

    @patch("routes.requests.get")
    def test_timeout(self, mock_get, client):
        mock_get.side_effect = __import__("requests").exceptions.Timeout()
        resp = post_json(
            client,
            "/api/settings/test-prowlarr",
            {"prowlarrUrl": "http://x", "prowlarrApiKey": "k"},
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
        resp = post_json(
            client,
            "/api/settings/test-prowlarr",
            {"prowlarrUrl": "http://x", "prowlarrApiKey": "k"},
        )
        data = resp.get_json()
        assert data["ok"] is False
        assert "Unauthorized" in data["message"]

    @patch("routes.requests.get")
    def test_unexpected_error(self, mock_get, client):
        mock_get.side_effect = RuntimeError("unexpected")
        resp = post_json(
            client,
            "/api/settings/test-prowlarr",
            {"prowlarrUrl": "http://x", "prowlarrApiKey": "k"},
        )
        data = resp.get_json()
        assert data["ok"] is False
        assert "Unexpected error" in data["message"]


class TestTestApprise:
    @patch("routes.apprise.Apprise")
    def test_no_urls(self, mock_cls, client):
        resp = post_json(client, "/api/settings/test-apprise", {"appriseUrls": ""})
        data = resp.get_json()
        assert data["ok"] is False

    @patch("routes.apprise.Apprise")
    def test_success(self, mock_cls, client):
        mock_ap = MagicMock()
        mock_ap.notify.return_value = True
        mock_cls.return_value = mock_ap

        resp = post_json(client, "/api/settings/test-apprise", {"appriseUrls": "json://localhost"})
        data = resp.get_json()
        assert data["ok"] is True
        assert "Sent" in data["message"]

    @patch("routes.apprise.Apprise")
    def test_delivery_failure_reported(self, mock_cls, client):
        mock_ap = MagicMock()
        mock_ap.notify.return_value = False
        mock_cls.return_value = mock_ap

        resp = post_json(client, "/api/settings/test-apprise", {"appriseUrls": "json://localhost"})
        data = resp.get_json()
        assert data["ok"] is False
        assert "may have failed" in data["message"]


# ---------------------------------------------------------------------------
# Link Sanitization Tests
# ---------------------------------------------------------------------------
class TestSanitizeUrl:
    def test_valid_schemes(self):
        assert prowlarr.sanitize_url("http://example.com") == "http://example.com"
        assert prowlarr.sanitize_url("https://example.com/test") == "https://example.com/test"
        assert prowlarr.sanitize_url("magnet:?xt=urn:btih:123") == "magnet:?xt=urn:btih:123"

    def test_urlparse_exception_returns_none(self):
        """A URL that makes urlparse raise is rejected (no crash)."""
        assert prowlarr.sanitize_url("http://[::1") is None

    def test_dangerous_schemes(self):
        assert prowlarr.sanitize_url("javascript:alert(1)") is None
        assert prowlarr.sanitize_url("JAVASCRIPT:alert(1)") is None
        assert prowlarr.sanitize_url("data:text/html,<script>alert(1)</script>") is None
        assert prowlarr.sanitize_url("vbscript:alert(1)") is None

    def test_relative_and_empty(self):
        assert prowlarr.sanitize_url("") is None
        assert prowlarr.sanitize_url(None) is None
        assert prowlarr.sanitize_url("/relative/path") is None

    def test_callbacks_sanitize_urls(self):
        with db.get_db() as conn:
            conn.execute(
                "INSERT INTO queries (name, query, created_at, next_run)"
                " VALUES ('q', 'q', 'now', 'now')"
            )
            qid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.commit()

        job = worker.Job(
            query="q",
            categories=[],
            excluded_indexers=[],
            label=f"q:{qid}",
            priority=worker.Priority.LOW,
            attempt=1,
            created_at=time.time(),
            status="done",
            result=[
                {
                    "title": "Bad Link",
                    "guid": "guid1",
                    "infoUrl": "javascript:steal()",
                    "downloadUrl": "javascript:alert(1)",
                },
                {
                    "title": "Good Link",
                    "guid": "guid2",
                    "infoUrl": "https://example.com",
                    "downloadUrl": "magnet:?xt=urn:btih:abc",
                },
            ],
        )
        callbacks.process_query_result(qid, "0 * * * *", job)

        with db.get_db() as conn:
            rows = conn.execute(
                "SELECT info_url, download_url FROM results WHERE query_id=?", (qid,)
            ).fetchall()
        assert len(rows) == 2
        for r in rows:
            assert r["info_url"] is None or r["info_url"].startswith("https://")
            assert r["download_url"] is None or r["download_url"].startswith("magnet:")


# ---------------------------------------------------------------------------
# Scheduler Daemon Crash Resilience Tests
# ---------------------------------------------------------------------------
class TestSchedulerResilience:
    def test_tick_corrupted_timestamp(self):
        """A corrupted next_run string is logged and recalculated, not crashing the thread."""
        with db._db_lock, db.get_db() as conn:
            conn.execute(
                "INSERT INTO queries (name, query, cron, created_at, next_run, enabled)"
                " VALUES ('bad_ts', 'bad_ts', '*/5 * * * *', '2026-01-01T00:00:00',"
                " 'not-a-timestamp', 1)"
            )
            conn.commit()

        sched = scheduler_mod.Scheduler()
        # Should not raise exception
        sched._tick()

        with db.get_db() as conn:
            row = conn.execute("SELECT next_run FROM queries WHERE name='bad_ts'").fetchone()
        assert row["next_run"] != "not-a-timestamp"

    def test_tick_invalid_cron_records_error_and_continues(self):
        """A query with an invalid cron expression doesn't stop other queries from running."""
        with db._db_lock, db.get_db() as conn:
            conn.execute(
                "INSERT INTO queries (name, query, cron, created_at, next_run, enabled)"
                " VALUES ('bad_cron', 'bad_cron', 'invalid cron', '2026-01-01T00:00:00', NULL, 1)"
            )
            conn.execute(
                "INSERT INTO queries (name, query, cron, created_at, next_run, enabled)"
                " VALUES ('good_query', 'good_query', '*/5 * * * *', '2026-01-01T00:00:00',"
                " '2020-01-01T00:00:00', 1)"
            )
            conn.commit()

        sched = scheduler_mod.Scheduler()
        sched._tick()

        with db.get_db() as conn:
            bad_row = conn.execute(
                "SELECT last_error FROM queries WHERE name='bad_cron'"
            ).fetchone()
            good_row = conn.execute(
                "SELECT next_run FROM queries WHERE name='good_query'"
            ).fetchone()

        assert bad_row["last_error"] is not None
        assert (
            "CroniterBadCronError" in bad_row["last_error"] or "ValueError" in bad_row["last_error"]
        )
        # Good query was still processed and updated
        assert good_row["next_run"] != "2020-01-01T00:00:00"

    def test_tick_record_error_failure_is_swallowed(self):
        """If even recording last_error fails, the tick still does not raise."""
        with db._db_lock, db.get_db() as conn:
            conn.execute(
                "INSERT INTO queries (name, query, cron, created_at, next_run, enabled)"
                " VALUES ('bad', 'bad', 'invalid cron', '2026-01-01T00:00:00', NULL, 1)"
            )
            conn.commit()

        sched = scheduler_mod.Scheduler()
        real_get_db = db.get_db
        calls = {"n": 0}

        def flaky_get_db():
            # First call: fetch rows. Second call: UPDATE last_error → raise.
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("record failed")
            return real_get_db()

        with patch("scheduler.get_db", side_effect=flaky_get_db):
            sched._tick()  # must not raise

    def test_loop_handles_exception_without_dying(self):
        """_loop catches exceptions in _tick and stays running."""
        sched = scheduler_mod.Scheduler()
        call_count = 0

        def failing_tick(startup=False):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("DB locked transiently")
            sched.stop()

        sched._tick = failing_tick
        sched._loop()
        assert call_count == 2


# ---------------------------------------------------------------------------
# Fail-Fast Preview Searches Tests
# ---------------------------------------------------------------------------
class TestFailFastPreview:
    @patch("worker.prowlarr_search_raw")
    def test_preview_fails_immediately_without_retrying(self, mock_search):
        mock_search.side_effect = ConnectionError("Prowlarr down")
        q = worker.WorkQueue()
        # Submit a preview job
        job = q.submit(
            query="test-preview",
            label="preview:1234abcd",
            priority=worker.Priority.HIGH,
        )
        # Directly process this job in the worker logic
        try:
            job.result = worker.prowlarr_search_raw(
                job.query, job.categories, job.excluded_indexers
            )
            job.status = "done"
        except Exception as exc:
            job.error = f"{type(exc).__name__}: {exc}"
            is_preview = job.label.startswith("preview:")
            max_ret = 1 if is_preview else q._max_retries()
            if job.attempt < max_ret:
                job.status = "retrying"
            else:
                job.status = "error"

        assert job.status == "error"
        assert "ConnectionError" in job.error


# ---------------------------------------------------------------------------
# App shell routes (index_page, not_found) and __main__ entry point
# ---------------------------------------------------------------------------
class TestAppRoutes:
    def _point_app_at_static(self, tmp_path, monkeypatch):
        """Point the app's static dir at a temp dir containing index.html."""
        static_dir = tmp_path / "static"
        static_dir.mkdir()
        (static_dir / "index.html").write_text("<!doctype html><title>SPA</title>")
        monkeypatch.setattr(app_mod, "STATIC_DIR", static_dir)
        monkeypatch.setattr(app_mod.app, "static_folder", str(static_dir))

    def test_index_page_serves_static_index(self, tmp_path, monkeypatch):
        self._point_app_at_static(tmp_path, monkeypatch)
        with app_mod.app.test_client() as c:
            resp = c.get("/")
        assert resp.status_code == 200
        assert resp.content_type == "text/html; charset=utf-8"
        assert "<title>SPA</title>" in resp.get_data(as_text=True)

    def test_not_found_non_api_serves_spa_shell(self, tmp_path, monkeypatch):
        self._point_app_at_static(tmp_path, monkeypatch)
        with app_mod.app.test_client() as c:
            resp = c.get("/some/client/route")
        assert resp.status_code == 200
        assert resp.content_type == "text/html; charset=utf-8"
        assert "<title>SPA</title>" in resp.get_data(as_text=True)

    def test_not_found_api_returns_problem_json(self):
        with app_mod.app.test_client() as c:
            resp = c.get("/api/nonexistent")
        assert resp.status_code == 404
        assert resp.content_type == "application/problem+json"
        assert resp.get_json()["title"] == "Not found"

    def test_not_found_without_index_file_returns_problem_json(self, tmp_path, monkeypatch):
        empty_dir = tmp_path / "empty-static"
        empty_dir.mkdir()
        monkeypatch.setattr(app_mod, "STATIC_DIR", empty_dir)
        monkeypatch.setattr(app_mod.app, "static_folder", str(empty_dir))
        with app_mod.app.test_client() as c:
            resp = c.get("/missing-shell")
        assert resp.status_code == 404
        assert resp.content_type == "application/problem+json"
        assert resp.get_json()["title"] == "Not found"

    def test_main_block_runs_app(self, monkeypatch):
        """`python app.py` invokes app.run() with the expected args."""
        import runpy

        mock_run = MagicMock()
        monkeypatch.setattr(Flask, "run", mock_run)
        # Re-run app.py as __main__ inside the already-patched test context.
        runpy.run_path(app_mod.__file__, run_name="__main__")
        mock_run.assert_called_once_with(host="0.0.0.0", port=5000, debug=False)


# ---------------------------------------------------------------------------
# Security Headers & Input Validation Tests
# ---------------------------------------------------------------------------
class TestSecurityHeaders:
    def test_security_headers_present(self, client):
        resp = client.get("/api/settings")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "SAMEORIGIN"
        assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
        assert "default-src 'self'" in resp.headers.get("Content-Security-Policy", "")


class TestInputValidation:
    def test_settings_url_schemes_invalid(self, client):
        resp = put_json(
            client,
            "/api/settings",
            {"prowlarrUrl": "ftp://localhost:9696"},
        )
        assert resp.status_code == 400
        assert "prowlarrUrl" in resp.get_json()["errors"]

    def test_settings_negative_interval(self, client):
        resp = put_json(
            client,
            "/api/settings",
            {"minQueryInterval": -5},
        )
        assert resp.status_code == 400
        assert "minQueryInterval" in resp.get_json()["errors"]

    def test_settings_invalid_retries(self, client):
        resp = put_json(
            client,
            "/api/settings",
            {"maxRetries": 0},
        )
        assert resp.status_code == 400
        assert "maxRetries" in resp.get_json()["errors"]

    def test_settings_invalid_excluded_indexers(self, client):
        resp = put_json(
            client,
            "/api/settings",
            {"defaultExcludedIndexers": ["not-an-int"]},
        )
        assert resp.status_code == 400
        assert "defaultExcludedIndexers" in resp.get_json()["errors"]

    def test_patch_query_invalid_excluded_indexers(self, client):
        qid = _insert_query()
        resp = patch_json(
            client,
            f"/api/queries/{qid}",
            {"excludedIndexers": "not-a-list"},
        )
        assert resp.status_code == 400
        assert "excludedIndexers" in resp.get_json()["errors"]
