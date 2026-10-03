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
                excluded_indexers  TEXT,
                last_new_result    TEXT,
                note               TEXT,
                audiobook          INTEGER NOT NULL DEFAULT 1,
                ebook              INTEGER NOT NULL DEFAULT 1
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
        if "last_new_result" not in cols:
            conn.execute("ALTER TABLE queries ADD COLUMN last_new_result TEXT")
            conn.commit()
        if "note" not in cols:
            conn.execute("ALTER TABLE queries ADD COLUMN note TEXT")
            conn.commit()
        if "audiobook" not in cols and "ebook" not in cols:
            conn.execute("ALTER TABLE queries ADD COLUMN audiobook INTEGER NOT NULL DEFAULT 1")
            conn.execute("ALTER TABLE queries ADD COLUMN ebook INTEGER NOT NULL DEFAULT 1")
            _migrate_note_media_flags(conn)
            conn.commit()

        # Backfill last_new_result from existing results
        row = conn.execute(
            "SELECT value FROM settings WHERE key='migrated_last_new_result_backfill'"
        ).fetchone()
        if not row:
            conn.execute("""
                UPDATE queries
                SET last_new_result = (
                    SELECT MAX(first_seen)
                    FROM results
                    WHERE results.query_id = queries.id
                )
                WHERE EXISTS (
                    SELECT 1 FROM results WHERE results.query_id = queries.id
                )
            """)
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                ("migrated_last_new_result_backfill", "1"),
            )
            conn.commit()

        _migrate_indexer_scoped_hashes(conn)


def split_note_media_flags(note: str | None) -> tuple[str | None, bool, bool]:
    """Pull "Audiobook" / "Ebook" comma-separated parts out of a note.

    Returns (cleaned note, audiobook, ebook). Neither or both present means both flags on.
    """
    parts = [p.strip() for p in (note or "").split(",")]
    audiobook = any(p.lower() == "audiobook" for p in parts)
    ebook = any(p.lower() == "ebook" for p in parts)
    kept = [p for p in parts if p and p.lower() not in ("audiobook", "ebook")]
    if not audiobook and not ebook:
        audiobook = ebook = True
    return (", ".join(kept) or None), audiobook, ebook


def _migrate_note_media_flags(conn: sqlite3.Connection):
    """One-time: derive audiobook/ebook flags from existing notes and strip those words."""
    for row in conn.execute("SELECT id, note FROM queries").fetchall():
        note, audiobook, ebook = split_note_media_flags(row["note"])
        conn.execute(
            "UPDATE queries SET note=?, audiobook=?, ebook=? WHERE id=?",
            (note, int(audiobook), int(ebook), row["id"]),
        )


def _migrate_indexer_scoped_hashes(conn: sqlite3.Connection):
    """Rewrite stored result hashes to include the indexer (see prowlarr.hash_result)."""
    key = "migrated_indexer_scoped_hashes"
    if conn.execute("SELECT 1 FROM settings WHERE key=?", (key,)).fetchone():
        return
    # Imported lazily: prowlarr imports this module.
    from prowlarr import hash_result

    cols = {r[1] for r in conn.execute("PRAGMA table_info(results)").fetchall()}
    if not {"title", "indexer", "size", "guid"} <= cols:
        return  # ancient schema without the columns needed to recompute hashes
    rows = conn.execute("SELECT id, title, indexer, size, guid FROM results").fetchall()
    conflicts = 0
    for row in rows:
        if not row["guid"] and row["title"] is None:
            continue  # nothing to recompute the hash from
        r = {"title": row["title"], "indexer": row["indexer"], "guid": row["guid"]}
        if row["size"] is not None:
            r["size"] = row["size"]
        cur = conn.execute(
            "UPDATE OR IGNORE results SET result_hash=? WHERE id=?", (hash_result(r), row["id"])
        )
        if cur.rowcount == 0:
            conflicts += 1
    if conflicts:
        log.warning(
            "Hash migration: %d result(s) kept their old hash due to a uniqueness conflict "
            "and may be re-notified as new",
            conflicts,
        )
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, '1')", (key,))
    conn.commit()


def get_setting(key: str, default: str = "") -> str:
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str):
    with _db_lock, get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, value))
        conn.commit()
