"""Informational notes about the calendar sync: a due date moved, or a due window
collides with something already booked. Nothing is ever written to Google from
these — they exist so a silent auto-update is still *visible* after the fact."""

import sqlite3
from datetime import datetime

DUE_MOVED = "due_moved"
CONFLICT = "conflict"
CAP = 50


class CanvasAlerts:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def add(self, kind: str, detail: str, assignment_id: int | None = None) -> int:
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO canvas_alerts (assignment_id, kind, detail, created_at) "
                "VALUES (?, ?, ?, ?)",
                (assignment_id, kind, detail,
                 datetime.now().isoformat(timespec="seconds")))
        return cur.lastrowid

    def recent(self, limit: int = 10) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM canvas_alerts WHERE seen = 0 "
            "ORDER BY id DESC LIMIT ?", (limit,))]

    def count(self) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM canvas_alerts WHERE seen = 0").fetchone()[0]

    def mark_seen(self, ids: list[int] | None = None) -> None:
        with self._conn:
            if ids:
                self._conn.executemany(
                    "UPDATE canvas_alerts SET seen = 1 WHERE id = ?",
                    [(i,) for i in ids])
            else:
                self._conn.execute("UPDATE canvas_alerts SET seen = 1")

    def prune(self, cap: int = CAP) -> None:
        """Notes are disposable; keep the tail bounded."""
        with self._conn:
            self._conn.execute(
                "DELETE FROM canvas_alerts WHERE id NOT IN "
                "(SELECT id FROM canvas_alerts ORDER BY id DESC LIMIT ?)", (cap,))
