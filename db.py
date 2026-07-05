"""Database setup, connection helpers, and settings access."""

import logging
import os
import sqlite3
import threading
from pathlib import Path

log = logging.getLogger("prowlarr-watcher")

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "watcher.db"

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
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                name               TEXT NOT NULL,
                query              TEXT NOT NULL,
                cron               TEXT,
                enabled            INTEGER NOT NULL DEFAULT 1,
                created_at         TEXT NOT NULL,
                last_run           TEXT,
                next_run           TEXT,
                last_count         INTEGER DEFAULT 0,
                last_error         TEXT,
                excluded_indexers  TEXT
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
        conn.execute("INSERT OR IGNORE INTO settings VALUES ('max_retries', '5')")
        conn.execute("INSERT OR IGNORE INTO settings VALUES ('prowlarr_timeout', '200')")
        conn.execute("INSERT OR IGNORE INTO settings VALUES ('default_excluded_indexers', '')")
        conn.commit()
        # Migrations for existing databases
        cols = {r[1] for r in conn.execute("PRAGMA table_info(queries)").fetchall()}
        if "last_error" not in cols:
            conn.execute("ALTER TABLE queries ADD COLUMN last_error TEXT")
            conn.commit()
        if "excluded_indexers" not in cols:
            conn.execute("ALTER TABLE queries ADD COLUMN excluded_indexers TEXT")
            conn.commit()


def get_setting(key: str, default: str = "") -> str:
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str):
    with _db_lock, get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, value))
        conn.commit()
