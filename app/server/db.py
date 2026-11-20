"""SQLite connection helpers and schema for the PD-F1009 catalog."""

from __future__ import annotations

import sqlite3
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = APP_DIR.parent
DEFAULT_DB_PATH = APP_DIR / "db" / "catalog.sqlite"
DEFAULT_CSV_PATH = APP_DIR / "db" / "cd_index.csv"

SLOT_MIN = 0
SLOT_MAX = 300

SCHEMA = """
CREATE TABLE IF NOT EXISTS albums (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_image TEXT NOT NULL,
    photo_index INTEGER NOT NULL,
    artist TEXT NOT NULL,
    title TEXT NOT NULL,
    needs_review INTEGER NOT NULL DEFAULT 0,
    discogs_release_id INTEGER,
    cover_path TEXT NOT NULL DEFAULT '',
    cover_jpeg BLOB,
    UNIQUE (source_image, photo_index)
);

CREATE TABLE IF NOT EXISTS discs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    disc_number INTEGER NOT NULL,
    source_image TEXT NOT NULL,
    photo_index INTEGER NOT NULL,
    UNIQUE (source_image, photo_index, disc_number)
);

CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    disc_id INTEGER NOT NULL REFERENCES discs(id) ON DELETE CASCADE,
    track_number INTEGER NOT NULL,
    artist TEXT NOT NULL,
    title TEXT NOT NULL,
    UNIQUE (disc_id, track_number)
);

CREATE TABLE IF NOT EXISTS slots (
    slot_number INTEGER PRIMARY KEY CHECK (slot_number >= 0 AND slot_number <= 300),
    disc_id INTEGER UNIQUE REFERENCES discs(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS play_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_number INTEGER NOT NULL,
    disc_id INTEGER,
    track_id INTEGER,
    track_number INTEGER,
    command TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_discs_album ON discs(album_id);
CREATE INDEX IF NOT EXISTS idx_tracks_disc ON tracks(disc_id);
CREATE INDEX IF NOT EXISTS idx_tracks_title ON tracks(title);
CREATE INDEX IF NOT EXISTS idx_albums_artist ON albums(artist);
CREATE INDEX IF NOT EXISTS idx_albums_title ON albums(title);
"""

SETTING_ARDUINO_BASE_URL = "arduino_base_url"


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # timeout waits on locks; busy_timeout makes concurrent cover reads resilient
    # when a phone loads many /api/covers/* requests at once.
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.Error:
        pass
    return conn


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] if isinstance(row, sqlite3.Row) else row[1] for row in rows}


def migrate_schema(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the first schema version."""
    album_cols = _existing_columns(conn, "albums")
    if album_cols and "discogs_release_id" not in album_cols:
        conn.execute("ALTER TABLE albums ADD COLUMN discogs_release_id INTEGER")


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    migrate_schema(conn)
    for slot in range(SLOT_MIN, SLOT_MAX + 1):
        conn.execute(
            "INSERT OR IGNORE INTO slots (slot_number, disc_id) VALUES (?, NULL)",
            (slot,),
        )
    conn.commit()


def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?",
        (key,),
    ).fetchone()
    if not row:
        return default
    return row["value"] if isinstance(row, sqlite3.Row) else row[0]


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )
    conn.commit()
