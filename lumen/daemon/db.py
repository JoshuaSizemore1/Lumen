"""SQLite bootstrap: connection setup + hand-written schema (no migration
framework). Called once at daemon startup; the daemon is the only writer."""

import os
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
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,                    -- derived from first user message, truncated
    created_at TEXT NOT NULL,              -- ISO timestamp
    updated_at TEXT NOT NULL,              -- ISO timestamp, bumped on every new turn
    tool_engaged INTEGER NOT NULL DEFAULT 0 -- 1 once any tool has run in this thread
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,                    -- 'user' | 'assistant'
    content TEXT NOT NULL,
    tool_calls TEXT,                       -- JSON array of tool names, nullable
    created_at TEXT NOT NULL               -- ISO timestamp
);
CREATE TABLE IF NOT EXISTS todo_suggestions (  -- LLM-extracted, pending user confirmation; never counted as todos
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    due_date TEXT,                          -- ISO local date, nullable
    quote TEXT NOT NULL,                    -- the promise phrase found verbatim in the email
    email_id TEXT NOT NULL,                 -- mirror message id (dedupe: never re-suggest)
    subject TEXT,
    status TEXT NOT NULL DEFAULT 'pending', -- pending | accepted | dismissed
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS emails (
    id TEXT PRIMARY KEY,                    -- Gmail message id
    thread_id TEXT,
    sender TEXT,                            -- display form: Name <addr>
    recipients TEXT,
    subject TEXT,
    body TEXT,                              -- plain text (text/plain part, else stripped HTML)
    body_html TEXT,                         -- raw text/html part; NULL = never fetched, '' = message has none
    snippet TEXT,
    labels TEXT,                            -- comma-separated Gmail label ids
    received_at TEXT,                       -- RFC3339 UTC from internalDate
    is_read INTEGER,
    attachments TEXT NOT NULL DEFAULT '[]', -- JSON [filename, …]; names only
    last_seen TEXT                          -- re-baseline run id; prunes gap-deleted rows
);
CREATE VIRTUAL TABLE IF NOT EXISTS emails_fts USING fts5(
    subject, body, snippet, sender, content='emails', content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS emails_ai AFTER INSERT ON emails BEGIN
    INSERT INTO emails_fts(rowid, subject, body, snippet, sender)
    VALUES (new.rowid, new.subject, new.body, new.snippet, new.sender);
END;
CREATE TRIGGER IF NOT EXISTS emails_ad AFTER DELETE ON emails BEGIN
    INSERT INTO emails_fts(emails_fts, rowid, subject, body, snippet, sender)
    VALUES ('delete', old.rowid, old.subject, old.body, old.snippet, old.sender);
END;
CREATE TRIGGER IF NOT EXISTS emails_au AFTER UPDATE ON emails BEGIN
    INSERT INTO emails_fts(emails_fts, rowid, subject, body, snippet, sender)
    VALUES ('delete', old.rowid, old.subject, old.body, old.snippet, old.sender);
    INSERT INTO emails_fts(rowid, subject, body, snippet, sender)
    VALUES (new.rowid, new.subject, new.body, new.snippet, new.sender);
END;
CREATE TABLE IF NOT EXISTS gmail_labels (   -- name↔id map cached from labels.list
    id TEXT PRIMARY KEY,                    -- Gmail label id (Label_… for user labels)
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'user'       -- 'system' | 'user'
);
CREATE TABLE IF NOT EXISTS mail_rules (     -- deterministic inbox rules (no LLM at run time)
    id INTEGER PRIMARY KEY,
    label TEXT NOT NULL,                    -- target label NAME (created in Gmail if missing)
    from_addrs TEXT NOT NULL DEFAULT '[]',  -- JSON arrays; a rule matches if ANY condition hits
    domains TEXT NOT NULL DEFAULT '[]',
    subject_kw TEXT NOT NULL DEFAULT '[]',
    body_kw TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_log (   -- Phase 9 tier-1 raw interaction log
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,               -- ISO timestamp
    subsystem TEXT NOT NULL,        -- calendar | email | todos | books | files | chat
    kind TEXT NOT NULL,             -- 'query' | 'correction'
    detail TEXT NOT NULL,           -- compact JSON: message, route, tools, outcome
    folded INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS canvas_courses (
    id INTEGER PRIMARY KEY,                 -- Canvas course id
    name TEXT NOT NULL,
    course_code TEXT,
    term TEXT,
    active INTEGER NOT NULL DEFAULT 1,       -- 1 = in the current active sync set
    last_synced TEXT                         -- ISO timestamp of last successful pull
);
CREATE TABLE IF NOT EXISTS canvas_assignments (
    id INTEGER PRIMARY KEY,                  -- Canvas assignment id
    course_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    due_at TEXT,                             -- RFC3339 UTC as given, nullable
    points REAL,
    html_url TEXT,
    description TEXT,                         -- HTML body, nullable
    submitted INTEGER NOT NULL DEFAULT 0,
    todo_id INTEGER,                          -- linked local todo (Part 4)
    calendar_event_id TEXT,                  -- linked calendar marker (Part 4)
    first_seen TEXT,                         -- ISO ts first observed (Part 4)
    handled INTEGER NOT NULL DEFAULT 0        -- user deleted the todo -> don't recreate
);
CREATE TABLE IF NOT EXISTS canvas_announcements (
    id INTEGER PRIMARY KEY,                  -- Canvas discussion_topic id
    course_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    posted_at TEXT,                          -- RFC3339 UTC
    message TEXT,                             -- HTML body
    html_url TEXT,
    seen INTEGER NOT NULL DEFAULT 0,          -- new-since-last-sync marker (Part 4/5)
    actionable INTEGER,                       -- NULL=unclassified, 0/1 after flag (Part 4)
    suggested_todo TEXT,                     -- JSON {text, due} suggestion (Part 4)
    todo_id INTEGER                           -- set if user accepted the offer (Part 4)
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    # The DB now mirrors verbatim chat transcripts — tighten it to owner-only,
    # same treatment the email mirror gets in Phase 6.
    try:
        os.chmod(db_path, 0o600)
    except OSError:
        pass
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    # Hand-written additive migrations for DBs created before a column existed
    # (CREATE IF NOT EXISTS never alters an existing table).
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(emails)")}
    if "body_html" not in cols:
        conn.execute("ALTER TABLE emails ADD COLUMN body_html TEXT")
    return conn
