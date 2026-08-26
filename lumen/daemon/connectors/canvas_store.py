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

    # Every synced column, with a default. executemany binds by name, so a
    # caller (or a fixture) that predates a column would raise without these —
    # and a widening upsert is exactly the change that quietly breaks a dozen
    # hand-built test dicts at once.
    _DEFAULTS = {"score": None, "updated_at": None, "submission_types": None,
                 "is_quiz": False}

    def upsert_assignments(self, rows: list[dict], stamp: str | None = None) -> None:
        """Insert or refresh the synced fields. `stamp` marks these rows as seen
        by this sync, which is how a vanished assignment is later detected — a
        plain upsert can never notice an absence."""
        if not rows:
            return
        rows = [{**self._DEFAULTS, **r,
                 "is_quiz": 1 if r.get("is_quiz") else 0,
                 "last_seen_sync": stamp} for r in rows]
        with self._conn:
            self._conn.executemany(
                "INSERT INTO canvas_assignments "
                "(id, course_id, name, due_at, points, score, html_url, description, "
                "submitted, updated_at, submission_types, is_quiz, last_seen_sync) "
                "VALUES (:id, :course_id, :name, :due_at, :points, :score, :html_url, "
                ":description, :submitted, :updated_at, :submission_types, :is_quiz, "
                ":last_seen_sync) "
                "ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name, due_at=excluded.due_at, points=excluded.points, "
                "score=excluded.score, "
                "html_url=excluded.html_url, description=excluded.description, "
                "submitted=excluded.submitted, updated_at=excluded.updated_at, "
                "submission_types=excluded.submission_types, is_quiz=excluded.is_quiz, "
                "last_seen_sync=COALESCE(excluded.last_seen_sync, last_seen_sync), "
                "missing_syncs=0",
                rows)

    def mark_missing(self, course_ids: list[int], stamp: str) -> int:
        """Count a consecutive absence for every assignment in a course we DID
        successfully fetch but which the payload no longer mentions.

        Two consecutive misses are required before anything is treated as gone
        (see canvas_events.GONE_STREAK): one flaky response that returns an empty
        list would otherwise read as "the whole term was deleted" and queue a
        mass calendar removal."""
        if not course_ids or not stamp:
            return 0
        marks = ",".join("?" * len(course_ids))
        with self._conn:
            cur = self._conn.execute(
                f"UPDATE canvas_assignments SET missing_syncs = missing_syncs + 1 "
                f"WHERE course_id IN ({marks}) "
                f"AND (last_seen_sync IS NULL OR last_seen_sync != ?)",
                [*course_ids, stamp])
        return cur.rowcount

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

    # --- calendar sync reads/writes -------------------------------------
    def calendar_candidates(self) -> list[dict]:
        """Every mirrored assignment joined to its course, WITHOUT the
        active/included/dismissed filter, plus the course label.

        Structural, not incidental: active_assignments() filters out precisely
        the rows that are the removal cases (archived course, dismissed item), so
        a diff engine fed from it would see them as absent rather than as
        "linked to an event that should now go". It cannot be reused here."""
        return [dict(r) for r in self._conn.execute(
            "SELECT a.*, c.course_code, c.name AS course_name, "
            "       COALESCE(c.active, 0) AS course_active, "
            "       COALESCE(c.included, 1) AS course_included "
            "FROM canvas_assignments a "
            "LEFT JOIN canvas_courses c ON c.id = a.course_id "
            "ORDER BY a.due_at IS NULL, a.due_at, a.id")]

    def set_calendar_event(self, assignment_id: int, event_id: str | None,
                           kind: str | None, start: str | None,
                           sig: str | None) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE canvas_assignments SET calendar_event_id = ?, "
                "event_kind = ?, event_start = ?, event_sig = ? WHERE id = ?",
                (event_id, kind, start, sig, assignment_id))

    def clear_calendar_event(self, assignment_id: int) -> None:
        """Forget the link without touching the assignment.

        Declining a removal MUST come through here. Otherwise the diff engine
        still sees an event linked to an ineligible assignment and re-queues the
        same removal on every single sync, forever."""
        self.set_calendar_event(assignment_id, None, None, None, None)

    def linked_events(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM canvas_assignments WHERE calendar_event_id IS NOT NULL")]

    # --- AI enrichment ---------------------------------------------------
    def unclassified_for_ai(self, limit: int) -> list[dict]:
        """Not-yet-classified rows worth spending a model call on: eligible-ish
        (has a due date, not submitted) and unclassified or stale."""
        return [dict(r) for r in self._conn.execute(
            "SELECT a.*, c.course_code, c.name AS course_name "
            "FROM canvas_assignments a "
            "JOIN canvas_courses c ON c.id = a.course_id "
            "WHERE a.ai_state = 0 AND a.due_at IS NOT NULL "
            "AND a.submitted = 0 AND a.dismissed = 0 "
            "AND c.active = 1 AND c.included = 1 "
            "ORDER BY a.due_at LIMIT ?", (limit,))]

    def set_ai_fields(self, assignment_id: int, summary: str | None,
                      ai_type: str | None, state: int = 1) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE canvas_assignments SET ai_summary = ?, ai_type = ?, "
                "ai_state = ? WHERE id = ?",
                (summary, ai_type, state, assignment_id))

    def reset_ai_state(self) -> None:
        """Turning the AI switch on re-offers everything for classification."""
        with self._conn:
            self._conn.execute("UPDATE canvas_assignments SET ai_state = 0")

    def pending_markers(self, active_ids: list[int] | None = None) -> list[dict]:
        """Assignments that need a calendar marker created or updated: has a
        linked todo, has a due date, not submitted, not handled. 'create' when no
        event yet; 'update' when the stored marker_due no longer matches the
        current local due date. active_ids scopes to the caller's active set.

        Events owned by the Canvas→Calendar sync are skipped outright: both
        paths share `calendar_event_id`, but only the sync sets `event_kind`,
        and only the legacy path sets `marker_due`. Without the skip a
        sync-created timed event reads as an "update" here (NULL marker_due
        never equals the due date) and "Add to calendar" would patch_all_day it
        into a flat all-day block behind the sync's back."""
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
            if a["calendar_event_id"] is not None and a.get("event_kind"):
                continue                    # owned by the calendar sync
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
