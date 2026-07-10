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
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT,
    date_finished TEXT,                     -- ISO date, nullable
    rating INTEGER,                         -- 1-5, nullable
    notes TEXT,                             -- nullable
    tags TEXT NOT NULL DEFAULT '[]',        -- JSON array of lowercase strings
    created_at TEXT NOT NULL                -- ISO timestamp
);
CREATE TABLE IF NOT EXISTS book_recs (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT,
    rationale TEXT,
    generated_at TEXT NOT NULL              -- ISO timestamp, same for the whole set
);
CREATE TABLE IF NOT EXISTS events (
    id TEXT NOT NULL,                       -- Google event/instance id
    calendar_id TEXT NOT NULL,
    calendar_name TEXT,
    color TEXT,                             -- calendar-level color, hex
    title TEXT,
    start_at TEXT NOT NULL,                 -- RFC3339 as given; ISO date if all-day
    end_at TEXT,
    all_day INTEGER NOT NULL DEFAULT 0,
    location TEXT,
    description TEXT,
    attendees TEXT NOT NULL DEFAULT '[]',   -- JSON [{email, name, self}]
    status TEXT,                            -- confirmed | tentative
    PRIMARY KEY (calendar_id, id)
);
CREATE TABLE IF NOT EXISTS sync_state (     -- KV; email sync shares it in Phase 6
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
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
