"""Email mirror: EmailStore (local full mirror the UI/chat read from) and
GmailSync (bounded bulk pull + History-API deltas via the API client — never
the LLM/MCP loop). See email-menu.md for the two-path strategy."""

import json
import sqlite3

HISTORY_KEY = "gmail_history_id"
CURSOR_KEY = "gmail_bulk_cursor"
WINDOW_KEY = "gmail_bulk_window_months"
LAST_SYNC_KEY = "gmail_last_sync"
RUN_KEY = "gmail_rebaseline_run"

_COLS = ("id", "thread_id", "sender", "recipients", "subject", "body", "snippet",
         "labels", "received_at", "is_read", "attachments", "last_seen")


class EmailStore:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def upsert(self, msgs: list[dict]) -> None:
        # UPSERT, never INSERT OR REPLACE — REPLACE bypasses the delete trigger
        # and silently corrupts the external-content FTS index.
        sets = ", ".join(f"{c} = excluded.{c}" for c in _COLS if c != "id")
        with self._conn:
            self._conn.executemany(
                f"INSERT INTO emails ({', '.join(_COLS)}) "
                f"VALUES ({', '.join('?' * len(_COLS))}) "
                f"ON CONFLICT(id) DO UPDATE SET {sets}",
                [tuple(
                    ",".join(m["labels"]) if c == "labels"
                    else json.dumps(m.get("attachments", [])) if c == "attachments"
                    else int(bool(m["is_read"])) if c == "is_read"
                    else m.get(c)
                    for c in _COLS) for m in msgs])

    def get(self, mid: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM emails WHERE id = ?", (mid,)).fetchone()
        return self._to_dict(row) if row else None

    def list_page(self, filter: str = "inbox", limit: int = 50, offset: int = 0) -> list[dict]:
        where = {"inbox": "WHERE (',' || labels || ',') LIKE '%,INBOX,%'",
                 "unread": "WHERE is_read = 0",
                 "all": ""}[filter]
        rows = self._conn.execute(
            f"SELECT * FROM emails {where} ORDER BY received_at DESC, id "
            f"LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return [self._to_dict(r) for r in rows]

    def search(self, query: str, limit: int = 50) -> list[dict]:
        # Quote each token so user input can't be parsed as FTS5 syntax.
        q = " ".join(f'"{t}"' for t in (query or "").replace('"', " ").split())
        if not q:
            return []
        rows = self._conn.execute(
            "SELECT e.* FROM emails_fts f JOIN emails e ON e.rowid = f.rowid "
            "WHERE emails_fts MATCH ? ORDER BY bm25(emails_fts) LIMIT ?",
            (q, limit)).fetchall()
        return [self._to_dict(r) for r in rows]

    def unread(self, limit: int = 10) -> list[dict]:
        return self.list_page("unread", limit=limit)

    def delete(self, mids: list[str]) -> None:
        with self._conn:
            self._conn.executemany("DELETE FROM emails WHERE id = ?",
                                   [(m,) for m in mids])

    def update_labels(self, mid: str, add: list[str], remove: list[str]) -> None:
        row = self._conn.execute("SELECT labels FROM emails WHERE id = ?", (mid,)).fetchone()
        if row is None:
            return
        labels = [l for l in (row["labels"] or "").split(",") if l and l not in remove]
        labels += [l for l in add if l not in labels]
        with self._conn:
            self._conn.execute(
                "UPDATE emails SET labels = ?, is_read = ? WHERE id = ?",
                (",".join(labels), int("UNREAD" not in labels), mid))

    def prune_not_seen(self, run_id: str, since_iso: str) -> int:
        with self._conn:
            cur = self._conn.execute(
                "DELETE FROM emails WHERE received_at >= ? "
                "AND (last_seen IS NULL OR last_seen != ?)", (since_iso, run_id))
        return cur.rowcount

    def counts(self) -> dict:
        row = self._conn.execute(
            "SELECT COUNT(*) AS total, "
            "COALESCE(SUM(CASE WHEN is_read = 0 THEN 1 END), 0) AS unread "
            "FROM emails").fetchone()
        return {"total": row["total"], "unread": row["unread"]}

    def get_state(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_state(self, key: str, value: str | None) -> None:
        with self._conn:
            if value is None:
                self._conn.execute("DELETE FROM sync_state WHERE key = ?", (key,))
            else:
                self._conn.execute(
                    "INSERT OR REPLACE INTO sync_state (key, value) VALUES (?, ?)",
                    (key, value))

    @staticmethod
    def _to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["labels"] = [l for l in (d["labels"] or "").split(",") if l]
        d["attachments"] = json.loads(d["attachments"] or "[]")
        d["is_read"] = bool(d["is_read"])
        return d
