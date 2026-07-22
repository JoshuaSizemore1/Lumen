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

    # --- archive switch (#28): user hides a course + stops pulling it, without
    # deleting anything. `included` is the user's flag; `active` is enrollment.
    def set_course_included(self, course_id: int, included: bool) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE canvas_courses SET included = ? WHERE id = ?",
                (1 if included else 0, course_id))

    def courses_for_panel(self) -> list[dict]:
        """Every enrolled course + its archive flag, for the Manage-courses panel.
        Archived courses stay listed so they can be switched back on."""
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM canvas_courses WHERE active = 1 "
            "ORDER BY course_code, name")]

    def pull_course_ids(self) -> list[int]:
        """Courses the poller should fetch: enrolled AND not archived."""
        return [r["id"] for r in self._conn.execute(
            "SELECT id FROM canvas_courses WHERE active = 1 AND included = 1")]

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
        # Hide announcements from archived courses (#28) and ones the user
        # dismissed. An announcement whose course row is absent is NOT archived,
        # so it still shows.
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM canvas_announcements "
            "WHERE dismissed = 0 AND course_id NOT IN "
            "(SELECT id FROM canvas_courses WHERE included = 0) "
            "ORDER BY posted_at DESC, id DESC LIMIT ?", (limit,))]

    # --- dismiss switch: user hides a single item from the tab, without
    # deleting anything. Survives sync (UPSERTs never touch `dismissed`).
    def set_assignment_dismissed(self, assignment_id: int, dismissed: bool) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE canvas_assignments SET dismissed = ? WHERE id = ?",
                (1 if dismissed else 0, assignment_id))

    def set_announcement_dismissed(self, ann_id: int, dismissed: bool) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE canvas_announcements SET dismissed = ? WHERE id = ?",
                (1 if dismissed else 0, ann_id))

    # --- reconciliation reads/writes (Part 4) ---
    def courses_by_id(self) -> dict[int, dict]:
        return {c["id"]: c for c in self.active_courses()}

    def active_assignments(self) -> list[dict]:
        # Enrolled AND not archived (#28) AND not dismissed — scopes the summary
        # list, reconcile, and pending calendar markers all at once, so a
        # dismissed item declutters the tab and drops out of the pending count.
        return [dict(r) for r in self._conn.execute(
            "SELECT a.* FROM canvas_assignments a "
            "JOIN canvas_courses c ON c.id = a.course_id "
            "WHERE c.active = 1 AND c.included = 1 AND a.dismissed = 0 "
            "ORDER BY a.due_at IS NULL, a.due_at, a.id")]

    def link_todo(self, assignment_id: int, todo_id: int, first_seen: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE canvas_assignments SET todo_id = ?, first_seen = ? "
                "WHERE id = ?", (todo_id, first_seen, assignment_id))

    def mark_handled(self, assignment_id: int) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE canvas_assignments SET handled = 1, todo_id = NULL "
                "WHERE id = ?", (assignment_id,))

    def set_calendar_marker(self, assignment_id: int, event_id: str | None,
                            marker_due: str | None) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE canvas_assignments "
                "SET calendar_event_id = ?, marker_due = ? WHERE id = ?",
                (event_id, marker_due, assignment_id))

    def pending_markers(self, active_ids: list[int] | None = None) -> list[dict]:
        """Assignments that need a calendar marker created or updated: has a
        linked todo, has a due date, not submitted, not handled. 'create' when no
        event yet; 'update' when the stored marker_due no longer matches the
        current local due date. active_ids scopes to the caller's active set."""
        from lumen.daemon.connectors.canvas_reconcile import local_day
        out: list[dict] = []
        for a in self.active_assignments():
            if active_ids is not None and a["course_id"] not in active_ids:
                continue
            if a["todo_id"] is None or a["handled"] or a["submitted"]:
                continue
            due = local_day(a["due_at"])
            if due is None:
                continue
            if a["calendar_event_id"] is None:
                out.append({**a, "action": "create", "due": due, "event_id": None})
            elif a["marker_due"] != due:
                out.append({**a, "action": "update", "due": due,
                            "event_id": a["calendar_event_id"]})
        return out

    # --- announcement classification (Part 4/5) ---
    def unclassified_announcements(self, limit: int) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM canvas_announcements WHERE actionable IS NULL "
            "ORDER BY posted_at DESC, id DESC LIMIT ?", (limit,))]

    def set_announcement_flag(self, ann_id: int, actionable: int,
                              suggested_todo: str | None) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE canvas_announcements "
                "SET actionable = ?, suggested_todo = ?, seen = 1 WHERE id = ?",
                (actionable, suggested_todo, ann_id))

    def mark_announcements_seen(self, ids: list[int]) -> None:
        if not ids:
            return
        with self._conn:
            self._conn.executemany(
                "UPDATE canvas_announcements SET seen = 1 WHERE id = ?",
                [(i,) for i in ids])

    def link_announcement_todo(self, ann_id: int, todo_id: int) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE canvas_announcements SET todo_id = ? WHERE id = ?",
                (todo_id, ann_id))

    def get_announcement(self, ann_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM canvas_announcements WHERE id = ?", (ann_id,)).fetchone()
        return dict(row) if row else None
