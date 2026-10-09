"""Local SQLite todo store: CRUD + the open-todos query the router injects
into chat. Mutations return the fresh full list so callers never re-fetch."""

import json
import sqlite3
from datetime import date, datetime

from lumen.daemon.connectors.todo_parse import parse_todo_input


class TodoStore:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def add(self, raw: str, today: date | None = None,
            source: str = "manual") -> list[dict]:
        text, due, tags = parse_todo_input(raw, today or date.today())
        if not text:
            raise ValueError("empty todo text")
        self._conn.execute(
            "INSERT INTO todos (text, due_date, created_at, source, tags) "
            "VALUES (?, ?, ?, ?, ?)",
            (text, due, datetime.now().isoformat(timespec="seconds"),
             source, json.dumps(tags)),
        )
        self._conn.commit()
        return self.list_all()

    def add_structured(self, text: str, due_date: str | None,
                       tags: list[str], source: str = "canvas") -> int:
        """Insert a todo with explicit fields (no NL parsing) and return its id.
        Used by reconciliation, which already knows text/due/tags."""
        if not text:
            raise ValueError("empty todo text")
        cur = self._conn.execute(
            "INSERT INTO todos (text, due_date, created_at, source, tags) "
            "VALUES (?, ?, ?, ?, ?)",
            (text, due_date, datetime.now().isoformat(timespec="seconds"),
             source, json.dumps(list(tags))))
        self._conn.commit()
        return cur.lastrowid

    def exists(self, todo_id: int) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM todos WHERE id = ?", (todo_id,)).fetchone() is not None

    def set_due(self, todo_id: int, due_date: str | None) -> None:
        self._conn.execute("UPDATE todos SET due_date = ? WHERE id = ?",
                           (due_date, todo_id))
        self._conn.commit()

    def list_all(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM todos ORDER BY created_at, id").fetchall()
        return [self._to_dict(r) for r in rows]

    _UNSET = object()

    def update(self, todo_id: int, *, text: str | None = None,
               description=_UNSET, due_date=_UNSET,
               tags: list[str] | None = None) -> list[dict]:
        """Partial edit of an existing todo (#23). Only the fields passed are
        written; due_date/description use a sentinel so passing None can clear
        them (distinct from "leave alone"). Returns the fresh full list."""
        sets, params = [], []
        if text is not None:
            t = text.strip()
            if not t:
                raise ValueError("empty todo text")
            sets.append("text = ?")
            params.append(t)
        if description is not self._UNSET:
            sets.append("description = ?")
            params.append((description or "").strip() or None)
        if due_date is not self._UNSET:
            sets.append("due_date = ?")
            params.append(due_date or None)
        if tags is not None:
            # Kept as the user typed them: force-lowering turned "AbellCRM"
            # into "abellcrm", so an edit appeared to rewrite the tag as well
            # as (apparently) losing the todo (#48). De-duped case-insensitively
            # — a tag typed two ways is still one tag — first spelling wins.
            cleaned = [s.strip() for s in tags if s and s.strip()]
            seen, uniq = set(), []
            for s in cleaned:
                if s.casefold() not in seen:
                    seen.add(s.casefold())
                    uniq.append(s)
            sets.append("tags = ?")
            params.append(json.dumps(uniq))
        if sets:
            params.append(todo_id)
            self._conn.execute(
                f"UPDATE todos SET {', '.join(sets)} WHERE id = ?", params)
            self._conn.commit()
        return self.list_all()

    def toggle(self, todo_id: int, completed: bool) -> list[dict]:
        self._conn.execute("UPDATE todos SET completed = ? WHERE id = ?",
                           (1 if completed else 0, todo_id))
        self._conn.commit()
        return self.list_all()

    def delete(self, todo_id: int) -> list[dict]:
        self._conn.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
        self._conn.commit()
        return self.list_all()

    def open_todos(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM todos WHERE completed = 0 "
            "ORDER BY due_date IS NULL, due_date, created_at, id").fetchall()
        return [self._to_dict(r) for r in rows]

    @staticmethod
    def _to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["completed"] = bool(d["completed"])
        d["tags"] = json.loads(d["tags"])
        return d
