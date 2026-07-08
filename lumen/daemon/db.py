"""SQLite bootstrap: connection setup + hand-written schema (no migration
framework). Called once at daemon startup; the daemon is the only writer."""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS todos (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    due_date TEXT,                          -- ISO local date, nullable
    completed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,               -- ISO timestamp
    source TEXT NOT NULL DEFAULT 'manual',  -- 'manual' | 'llm-extracted' | 'email' | 'calendar'
    tags TEXT NOT NULL DEFAULT '[]'         -- JSON array of lowercase strings
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn
