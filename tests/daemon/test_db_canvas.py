import pytest

from lumen.daemon import db


def _tables(conn):
    return {r["name"] for r in
            conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_canvas_tables_created(tmp_path):
    conn = db.connect(tmp_path / "c.db")
    assert {"canvas_courses", "canvas_assignments",
            "canvas_announcements"} <= _tables(conn)


def test_canvas_assignments_has_reconciliation_columns(tmp_path):
    conn = db.connect(tmp_path / "c.db")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(canvas_assignments)")}
    assert {"todo_id", "calendar_event_id", "first_seen", "handled",
            "submitted", "due_at"} <= cols


def test_canvas_assignments_has_marker_due(tmp_path):
    conn = db.connect(tmp_path / "c.db")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(canvas_assignments)")}
    assert "marker_due" in cols


# --- calendar-sync schema ----------------------------------------------------
CALENDAR_COLUMNS = {"event_kind", "event_start", "event_sig", "last_seen_sync",
                    "missing_syncs", "updated_at", "submission_types", "is_quiz",
                    "ai_summary", "ai_type", "ai_state"}


def test_calendar_sync_tables_created(tmp_path):
    conn = db.connect(tmp_path / "c.db")
    assert {"canvas_calendar_queue", "canvas_proposals",
            "canvas_alerts"} <= _tables(conn)


def test_canvas_assignments_has_calendar_columns(tmp_path):
    conn = db.connect(tmp_path / "c.db")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(canvas_assignments)")}
    assert CALENDAR_COLUMNS <= cols


def test_calendar_columns_are_added_to_an_older_db(tmp_path):
    """The additive-migration path: a DB created before these columns existed
    must gain them, because CREATE TABLE IF NOT EXISTS never alters one."""
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE canvas_assignments ("
                "id INTEGER PRIMARY KEY, course_id INTEGER NOT NULL, "
                "name TEXT NOT NULL, due_at TEXT, submitted INTEGER NOT NULL "
                "DEFAULT 0)")
    old.execute("INSERT INTO canvas_assignments (id, course_id, name) "
                "VALUES (1, 1, 'HW1')")
    old.commit()
    old.close()

    conn = db.connect(path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(canvas_assignments)")}
    assert CALENDAR_COLUMNS <= cols
    row = conn.execute("SELECT * FROM canvas_assignments WHERE id = 1").fetchone()
    assert row["name"] == "HW1"           # the existing row survives
    assert row["missing_syncs"] == 0 and row["ai_state"] == 0


def test_pending_removal_index_blocks_duplicates(tmp_path):
    """The background poll re-proposes the same removal every tick; the partial
    unique index is what stops the queue growing without bound."""
    import sqlite3
    conn = db.connect(tmp_path / "c.db")
    row = ("assignment", "evt_1", "CS — HW1", "submitted", "2026-08-25T00:00:00")
    conn.execute("INSERT INTO canvas_calendar_queue "
                 "(kind, event_id, title, reason, created_at) VALUES (?,?,?,?,?)", row)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO canvas_calendar_queue "
                     "(kind, event_id, title, reason, created_at) VALUES (?,?,?,?,?)", row)
    # ...but once resolved, the same event may be queued again later.
    conn.execute("UPDATE canvas_calendar_queue SET status = 'declined'")
    conn.execute("INSERT INTO canvas_calendar_queue "
                 "(kind, event_id, title, reason, created_at) VALUES (?,?,?,?,?)", row)
    assert conn.execute("SELECT COUNT(*) FROM canvas_calendar_queue").fetchone()[0] == 2
