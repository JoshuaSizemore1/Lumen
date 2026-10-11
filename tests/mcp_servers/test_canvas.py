"""canvas MCP server: list_assignments/get_assignment/list_announcements over
the local Canvas mirror. Tests the underscored impl functions against a real
tmp-file DB — the mirror is just SQLite, no fakes needed."""

from lumen.daemon import db
from lumen.mcp_servers import canvas as srv


def _conn(tmp_path):
    conn = db.connect(tmp_path / "c.db")
    conn.execute("INSERT INTO canvas_courses (id, name, course_code, active) "
                 "VALUES (1, 'CS 3505', 'CS3505', 1)")
    conn.execute("INSERT INTO canvas_assignments (id, course_id, name, due_at, "
                 "points, html_url, description, submitted) VALUES "
                 "(10, 1, 'HW1', '2026-09-01T06:59:59Z', 100, 'http://u', 'body', 0)")
    conn.execute("INSERT INTO canvas_announcements (id, course_id, title, "
                 "posted_at, message, html_url) VALUES "
                 "(5, 1, 'Welcome', '2026-08-20T00:00:00Z', 'hello class', 'http://a')")
    conn.commit()
    return conn


def test_list_assignments(tmp_path):
    out = srv._list_assignments(_conn(tmp_path), "", 20)
    assert "HW1" in out and "CS3505" in out and "id=10" in out


def test_list_assignments_course_filter(tmp_path):
    conn = _conn(tmp_path)
    assert "HW1" in srv._list_assignments(conn, "cs3505", 20)
    assert "HW1" not in srv._list_assignments(conn, "math", 20)


def test_get_assignment(tmp_path):
    out = srv._get_assignment(_conn(tmp_path), "10")
    assert "HW1" in out and "100" in out and "http://u" in out


def test_get_assignment_missing(tmp_path):
    assert "No assignment" in srv._get_assignment(_conn(tmp_path), "999")


def test_list_announcements(tmp_path):
    out = srv._list_announcements(_conn(tmp_path), 10)
    assert "Welcome" in out and "CS3505" in out


def test_list_assignments_puts_upcoming_first_and_hides_gone_ones(tmp_path):
    """#131 made this reachable from chat: "what's due this week?" must not
    get the term's oldest work, nor what the Canvas tab hides (#127)."""
    from datetime import datetime, timedelta, timezone
    conn = _conn(tmp_path)                       # HW1: long past

    def due(days):
        return (datetime.now(timezone.utc) + timedelta(days=days)
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.executemany(
        "INSERT INTO canvas_assignments (id, course_id, name, due_at, submitted, "
        "dismissed, missing_syncs) VALUES (?, 1, ?, ?, 0, ?, ?)",
        [(11, "Soon", due(2), 0, 0), (12, "Later", due(9), 0, 0),
         (13, "Dismissed", due(3), 1, 0), (14, "Gone", due(4), 0, 9)])
    conn.commit()
    names = [line.split(": ", 1)[1] for line in
             srv._list_assignments(conn, "", 20).splitlines()]
    assert names == ["Soon", "Later", "HW1"]
