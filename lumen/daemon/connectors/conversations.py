"""Local SQLite conversation store: a verbatim transcript log of chat threads.

This is deliberately dumb — it stores turns exactly as they happened and never
summarizes them. It is NOT the Phase 9 memory system (no distillation, no capped
blob). The daemon owns all conversation state; the UI only renders what this returns.
"""

import json
import sqlite3
from datetime import datetime

TITLE_MAX = 60


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class ConversationStore:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def create(self, first_message: str) -> int:
        """Open a new conversation, titled from the first line of the first message."""
        title = first_message.strip().splitlines()[0][:TITLE_MAX] if first_message.strip() else "New chat"
        now = _now()
        cur = self._conn.execute(
            "INSERT INTO conversations (title, created_at, updated_at) VALUES (?, ?, ?)",
            (title, now, now))
        self._conn.commit()
        return cur.lastrowid

    def add_message(self, conv_id: int, role: str, content: str,
                    tool_calls: list[str] | None = None) -> None:
        now = _now()
        self._conn.execute(
            "INSERT INTO messages (conversation_id, role, content, tool_calls, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (conv_id, role, content,
             json.dumps(tool_calls) if tool_calls else None, now))
        self._conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?",
                           (now, conv_id))
        self._conn.commit()

    def history(self, conv_id: int, limit: int | None = None) -> list[dict]:
        """Role/content turns in chronological order, ready to splice into a prompt.
        `limit` keeps only the most recent N turns (still in order) for the context
        budget; older turns stay on disk."""
        rows = self._conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? "
            "ORDER BY id", (conv_id,)).fetchall()
        turns = [{"role": r["role"], "content": r["content"]} for r in rows]
        return turns[-limit:] if limit is not None else turns

    def list_recent(self, limit: int = 50) -> list[dict]:
        """Sidebar-shaped rows (no bodies), newest activity first."""
        rows = self._conn.execute(
            "SELECT id, title, updated_at FROM conversations "
            "ORDER BY updated_at DESC, id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get(self, conv_id: int) -> dict | None:
        """Full thread (conversation + every message) for reopening, or None."""
        crow = self._conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
        if crow is None:
            return None
        mrows = self._conn.execute(
            "SELECT id, role, content, tool_calls, created_at FROM messages "
            "WHERE conversation_id = ? ORDER BY id", (conv_id,)).fetchall()
        return {"conversation": self._conv_dict(crow),
                "messages": [self._msg_dict(m) for m in mrows]}

    def delete(self, conv_id: int) -> None:
        self._conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        self._conn.commit()

    def mark_tool_engaged(self, conv_id: int) -> None:
        self._conn.execute(
            "UPDATE conversations SET tool_engaged = 1 WHERE id = ?", (conv_id,))
        self._conn.commit()

    def is_tool_engaged(self, conv_id: int) -> bool:
        row = self._conn.execute(
            "SELECT tool_engaged FROM conversations WHERE id = ?", (conv_id,)).fetchone()
        return bool(row["tool_engaged"]) if row is not None else False

    @staticmethod
    def _conv_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["tool_engaged"] = bool(d["tool_engaged"])
        return d

    @staticmethod
    def _msg_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["tool_calls"] = json.loads(d["tool_calls"]) if d["tool_calls"] else None
        return d
