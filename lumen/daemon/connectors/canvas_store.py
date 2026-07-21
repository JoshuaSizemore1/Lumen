"""Local SQLite mirror of Canvas courses/assignments/announcements. Read + upsert
only; the daemon is the sole writer. Same shape as EmailStore. UPSERTs update only
the synced fields, leaving reconciliation columns (todo_id, calendar_event_id,
first_seen, handled, seen, actionable, suggested_todo) untouched."""

import sqlite3


class CanvasStore:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def upsert_courses(self, courses: list[dict]) -> None:
        if not courses:
            return
        with self._conn:
            self._conn.executemany(
                "INSERT INTO canvas_courses (id, name, course_code, active) "
                "VALUES (:id, :name, :course_code, 1) "
                "ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name, course_code=excluded.course_code, active=1",
                courses)

    def deactivate_courses_except(self, keep_ids: list[int]) -> None:
        """Mark every course NOT in keep_ids inactive, so concluded courses drop
        out of active_courses() once they leave the live active-enrollment set.
        Empty keep_ids (e.g. between terms) deactivates all."""
        with self._conn:
            if keep_ids:
                placeholders = ",".join("?" * len(keep_ids))
                self._conn.execute(
                    f"UPDATE canvas_courses SET active = 0 "
                    f"WHERE id NOT IN ({placeholders})", keep_ids)
            else:
                self._conn.execute("UPDATE canvas_courses SET active = 0")

    def upsert_assignments(self, rows: list[dict]) -> None:
        if not rows:
            return
        with self._conn:
            self._conn.executemany(
                "INSERT INTO canvas_assignments "
                "(id, course_id, name, due_at, points, html_url, description, submitted) "
                "VALUES (:id, :course_id, :name, :due_at, :points, :html_url, "
                ":description, :submitted) "
                "ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name, due_at=excluded.due_at, points=excluded.points, "
                "html_url=excluded.html_url, description=excluded.description, "
                "submitted=excluded.submitted",
                rows)

    def upsert_announcements(self, rows: list[dict]) -> None:
        if not rows:
            return
        with self._conn:
            self._conn.executemany(
                "INSERT INTO canvas_announcements "
                "(id, course_id, title, posted_at, message, html_url) "
                "VALUES (:id, :course_id, :title, :posted_at, :message, :html_url) "
                "ON CONFLICT(id) DO UPDATE SET "
                "title=excluded.title, posted_at=excluded.posted_at, "
                "message=excluded.message, html_url=excluded.html_url",
                rows)

    def active_courses(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM canvas_courses WHERE active = 1 ORDER BY id DESC")]

    def assignments(self, course_id: int | None = None) -> list[dict]:
        if course_id is None:
            rows = self._conn.execute(
                "SELECT * FROM canvas_assignments "
                "ORDER BY due_at IS NULL, due_at, id")
        else:
            rows = self._conn.execute(
                "SELECT * FROM canvas_assignments WHERE course_id = ? "
                "ORDER BY due_at IS NULL, due_at, id", (course_id,))
        return [dict(r) for r in rows]

    def announcements(self, limit: int = 50) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM canvas_announcements ORDER BY posted_at DESC, id DESC "
            "LIMIT ?", (limit,))]
