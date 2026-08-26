"""AI-inferred calendar events — exam dates mined from announcement text, and
study blocks. Modelled on SuggestionStore.

These NEVER auto-create. A date the model read out of prose is a guess, and a
wrong guess written straight to the user's real calendar is worse than no
feature; every one is approved item-by-item. Dismissed stays dismissed."""

import sqlite3
from datetime import datetime


class CanvasProposals:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def add(self, kind: str, title: str, start_at: str, *, course_id=None,
            end_at=None, detail=None, source_kind=None, source_id=None) -> int | None:
        """None when an identical proposal is already pending (the poll re-reads
        the same announcement every tick) or was already dismissed."""
        # Study blocks are refused wholesale for their assignment; everything
        # else only for the exact time it was refused at.
        refused_at = None if kind == "study" else start_at
        if self.was_dismissed(kind, source_kind, source_id, refused_at):
            return None
        try:
            with self._conn:
                cur = self._conn.execute(
                    "INSERT INTO canvas_proposals (course_id, kind, title, start_at, "
                    "end_at, detail, source_kind, source_id, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (course_id, kind, title, start_at, end_at, detail, source_kind,
                     source_id, datetime.now().isoformat(timespec="seconds")))
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None

    def was_dismissed(self, kind, source_kind, source_id, start_at) -> bool:
        """A proposal the user said no to must not come back on the next sync.

        `start_at=None` matches ANY time for that source. Study blocks need that:
        their time comes from whatever slot is free, so it moves as the calendar
        fills, and keying the refusal on the exact time means dismissing an 08:00
        block just brings it back at 11:00. Exams key on the time, because a
        different date really is a different claim about the world."""
        sql = ("SELECT 1 FROM canvas_proposals WHERE kind = ? AND source_kind IS ? "
               "AND source_id IS ? AND status = 'dismissed'")
        args = [kind, source_kind, source_id]
        if start_at is not None:
            sql += " AND start_at = ?"
            args.append(start_at)
        return self._conn.execute(sql, args).fetchone() is not None

    def pending(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM canvas_proposals WHERE status = 'pending' "
            "ORDER BY start_at, id")]

    def count(self) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM canvas_proposals WHERE status = 'pending'"
        ).fetchone()[0]

    def get(self, pid: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM canvas_proposals WHERE id = ? AND status = 'pending'",
            (pid,)).fetchone()
        return dict(row) if row else None

    def accept(self, pid: int, event_id: str | None) -> bool:
        with self._conn:
            cur = self._conn.execute(
                "UPDATE canvas_proposals SET status = 'accepted', event_id = ? "
                "WHERE id = ? AND status = 'pending'", (event_id, pid))
        return cur.rowcount > 0

    def dismiss(self, pid: int) -> bool:
        with self._conn:
            cur = self._conn.execute(
                "UPDATE canvas_proposals SET status = 'dismissed' "
                "WHERE id = ? AND status = 'pending'", (pid,))
        return cur.rowcount > 0

    def has_exam_on(self, course_id: int, day: str) -> bool:
        """Dedup guard for exam mining: one exam per course per day is plenty."""
        row = self._conn.execute(
            "SELECT 1 FROM canvas_proposals WHERE course_id = ? AND kind = 'exam' "
            "AND date(start_at) = ? AND status != 'dismissed'",
            (course_id, day)).fetchone()
        return row is not None
