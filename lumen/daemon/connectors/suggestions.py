"""LLM-extracted todo suggestions (commitment tracking). Kept apart from the
todos table so nothing unconfirmed ever counts as a todo; accept promotes,
dismiss buries, and an email id present here is never re-suggested."""

import sqlite3
from datetime import datetime

LAST_SCAN_KEY = "commitments_last_scan"


class SuggestionStore:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def add(self, text: str, due_date: str | None, quote: str,
            email_id: str, subject: str | None) -> int:
        cur = self._conn.execute(
            "INSERT INTO todo_suggestions (text, due_date, quote, email_id, "
            "subject, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (text, due_date, quote, email_id, subject,
             datetime.now().isoformat(timespec="seconds")))
        self._conn.commit()
        return cur.lastrowid

    def pending(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM todo_suggestions WHERE status = 'pending' "
            "ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def accept(self, sid: int) -> dict | None:
        """Mark accepted and hand back the row for promotion; None if the id
        isn't pending (double click, stale UI)."""
        row = self._conn.execute(
            "SELECT * FROM todo_suggestions WHERE id = ? AND status = 'pending'",
            (sid,)).fetchone()
        if row is None:
            return None
        self._conn.execute(
            "UPDATE todo_suggestions SET status = 'accepted' WHERE id = ?", (sid,))
        self._conn.commit()
        return dict(row)

    def dismiss(self, sid: int) -> None:
        self._conn.execute(
            "UPDATE todo_suggestions SET status = 'dismissed' "
            "WHERE id = ? AND status = 'pending'", (sid,))
        self._conn.commit()

    def has_email(self, email_id: str) -> bool:
        """Any status counts — a dismissed promise must stay dismissed."""
        return self._conn.execute(
            "SELECT 1 FROM todo_suggestions WHERE email_id = ? LIMIT 1",
            (email_id,)).fetchone() is not None

    def last_scan(self) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM sync_state WHERE key = ?", (LAST_SCAN_KEY,)).fetchone()
        return row["value"] if row else None

    def set_last_scan(self, ts_iso: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO sync_state (key, value) VALUES (?, ?)",
                (LAST_SCAN_KEY, ts_iso))
