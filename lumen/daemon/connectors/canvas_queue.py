"""Proposed calendar REMOVALS, awaiting the user.

The background poll only ever *proposes*. ConfirmBroker.wait treats its 120s
timeout as a DENY, so a modal fired from a headless sync loop with no UI open is
worse than useless — it would silently answer "no" to a question nobody saw. So a
removal is written here and surfaced as a review strip, then executed inside a
request the user actually initiated.

Silent create/update is the explicitly-decided exception to that rule: only a
disappearance needs assent."""

import sqlite3
from datetime import datetime


class CanvasQueue:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def add(self, assignment_id: int | None, event_id: str, title: str,
            reason: str, detail: str | None = None,
            kind: str = "assignment") -> int | None:
        """Queue one removal. Returns None when an identical one is already
        pending — the partial unique index is what stops the background poll from
        re-proposing the same removal forever."""
        try:
            with self._conn:
                cur = self._conn.execute(
                    "INSERT INTO canvas_calendar_queue "
                    "(assignment_id, kind, event_id, title, reason, detail, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (assignment_id, kind, event_id, title, reason, detail,
                     datetime.now().isoformat(timespec="seconds")))
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None

    def pending(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM canvas_calendar_queue WHERE status = 'pending' "
            "ORDER BY id")]

    def count(self) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM canvas_calendar_queue "
            "WHERE status = 'pending'").fetchone()[0]

    def get(self, qid: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM canvas_calendar_queue WHERE id = ? AND status = 'pending'",
            (qid,)).fetchone()
        return dict(row) if row else None

    def resolve(self, qid: int, status: str) -> bool:
        """'removed' or 'declined'. False if it wasn't pending (double click,
        stale UI) — which keeps a second click from deleting something twice."""
        with self._conn:
            cur = self._conn.execute(
                "UPDATE canvas_calendar_queue SET status = ? "
                "WHERE id = ? AND status = 'pending'", (status, qid))
        return cur.rowcount > 0

    def drop_for_assignment(self, assignment_id: int) -> None:
        """The assignment became eligible again before the user answered, so the
        question is moot."""
        with self._conn:
            self._conn.execute(
                "UPDATE canvas_calendar_queue SET status = 'declined' "
                "WHERE assignment_id = ? AND status = 'pending'", (assignment_id,))
