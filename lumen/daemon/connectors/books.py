"""Local book catalog (reading log): CRUD + the catalog context the LLM sees.
Recommendation grounding lives in daemon/llm/book_recs.py — every suggestion must
trace back to an actual lookup result, never invented from model training data."""

import json
import sqlite3
from datetime import date, datetime


class BookStore:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def add(self, title: str, author: str | None = None, rating: int | None = None,
            notes: str | None = None, date_finished: str | None = None) -> list[dict]:
        title = (title or "").strip()
        if not title:
            raise ValueError("empty book title")
        if rating is not None and not 1 <= rating <= 5:
            raise ValueError("rating must be 1-5")
        self._conn.execute(
            "INSERT INTO books (title, author, date_finished, rating, notes, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (title, (author or "").strip() or None,
             date_finished or date.today().isoformat(), rating,
             (notes or "").strip() or None,
             datetime.now().isoformat(timespec="seconds")))
        self._conn.commit()
        return self.list_all()

    def list_all(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM books ORDER BY date_finished IS NULL, date_finished DESC, "
            "created_at DESC, id DESC").fetchall()
        return [self._to_dict(r) for r in rows]

    def delete(self, book_id: int) -> list[dict]:
        self._conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
        self._conn.commit()
        return self.list_all()

    def catalog_context(self) -> str:
        """System-message context: the whole log, or an explicit empty marker so
        the model can't hallucinate around a blank catalog."""
        books = self.list_all()
        if not books:
            return "The user's reading log is empty."
        lines = ["The user's reading log (books they have read):"]
        for b in books:
            author = f" by {b['author']}" if b["author"] else ""
            rating = f" — rated {b['rating']}/5" if b["rating"] else ""
            notes = f" — notes: {b['notes']}" if b["notes"] else ""
            lines.append(f"- {b['title']}{author}{rating}{notes}")
        return "\n".join(lines)

    @staticmethod
    def _to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["tags"] = json.loads(d["tags"])
        return d
