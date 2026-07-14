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

    def list_all(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM todos ORDER BY created_at, id").fetchall()
        return [self._to_dict(r) for r in rows]

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
