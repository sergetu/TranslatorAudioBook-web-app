"""SQLite bootstrap: connection, schema, settings seed."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'user',          -- user | admin
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    source_lang TEXT NOT NULL DEFAULT 'zh',
    target_lang TEXT NOT NULL DEFAULT 'ru',
    mode TEXT NOT NULL DEFAULT 'chaptered',      -- chaptered | stream
    translator TEXT NOT NULL DEFAULT 'local',    -- local | deepseek
    status TEXT NOT NULL DEFAULT 'empty',        -- empty|chunked|translated|tts_done
    source_filename TEXT NOT NULL DEFAULT '',
    source_encoding TEXT NOT NULL DEFAULT '',
    cover_path TEXT NOT NULL DEFAULT '',
    source_chars INTEGER NOT NULL DEFAULT 0,
    chapters_total INTEGER NOT NULL DEFAULT 0,
    legacy_audio_dir TEXT NOT NULL DEFAULT '',   -- для stream-книг
    legacy_ru_file TEXT NOT NULL DEFAULT '',     -- полный перевод «как был» при конвертации stream->chaptered
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chapters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    num INTEGER NOT NULL,                        -- 1..N
    title TEXT NOT NULL DEFAULT '',
    source_chars INTEGER NOT NULL DEFAULT 0,
    zh_path TEXT NOT NULL DEFAULT '',
    ru_path TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'none',         -- none|queued|translated|error
    error TEXT NOT NULL DEFAULT '',
    tts_status TEXT NOT NULL DEFAULT 'none',     -- none|queued|done|error
    tts_error TEXT NOT NULL DEFAULT '',
    audio_parts INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    UNIQUE(book_id, num)
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL,
    chapter_id INTEGER,
    owner_id INTEGER NOT NULL,
    type TEXT NOT NULL,                          -- translate|repair|revise|tts
    status TEXT NOT NULL DEFAULT 'queued',       -- queued|running|done|error|canceled
    priority INTEGER NOT NULL DEFAULT 5,
    progress REAL NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',               -- feedback пользователя (для revise)
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS progress (
    user_id INTEGER NOT NULL,
    book_id INTEGER NOT NULL,
    chapter_num INTEGER NOT NULL DEFAULT 1,
    position_sec REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, book_id)
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    config._ensure_dirs()  # noqa: SLF001
    with connect() as conn:
        conn.executescript(SCHEMA)
        # лёгкие миграции для уже созданных БД
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(books)")}
        if "legacy_ru_file" not in cols:
            conn.execute("ALTER TABLE books ADD COLUMN legacy_ru_file TEXT NOT NULL DEFAULT ''")
        jcols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
        if "note" not in jcols:
            conn.execute("ALTER TABLE jobs ADD COLUMN note TEXT NOT NULL DEFAULT ''")
        existing = {r["key"] for r in conn.execute("SELECT key FROM settings")}
        for key, value in config.DEFAULT_SETTINGS.items():
            if key not in existing:
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?)", (key, value)
                )


def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    return row["value"]


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def utcnow() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def conn_ctx() -> Iterator[sqlite3.Connection]:
    """Context manager: fresh connection per use (thread-safe with SQLite WAL)."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
