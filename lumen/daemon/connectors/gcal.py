"""Google Calendar: EventStore (local window cache the UI/chat read from) and
CalendarSync (rolling-window poll via the API client — never the LLM/MCP loop).
Store what the API returns; render local at the edges."""

import json
import sqlite3

LAST_SYNC_KEY = "calendar_last_sync"

COLUMNS = ("id", "calendar_id", "calendar_name", "color", "title", "start_at",
           "end_at", "all_day", "location", "description", "attendees", "status")


class EventStore:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def replace_window(self, events: list[dict], start_iso: str, end_iso: str) -> None:
        """Whole-window refresh in one transaction — small dataset, no diffing."""
        with self._conn:
            self._conn.execute(
                "DELETE FROM events WHERE date(start_at) >= ? AND date(start_at) <= ?",
                (start_iso, end_iso))
            self._conn.executemany(
                f"INSERT OR REPLACE INTO events ({', '.join(COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(COLUMNS))})",
                [tuple(
                    json.dumps(e["attendees"]) if c == "attendees"
                    else int(bool(e["all_day"])) if c == "all_day"
                    else e.get(c)
                    for c in COLUMNS) for e in events])

    def list_range(self, start_iso: str, end_iso: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM events WHERE date(start_at) >= ? AND date(start_at) <= ? "
            "ORDER BY date(start_at), all_day DESC, start_at, id",
            (start_iso, end_iso)).fetchall()
        return [self._to_dict(r) for r in rows]

    def set_last_sync(self, ts_iso: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO sync_state (key, value) VALUES (?, ?)",
                (LAST_SYNC_KEY, ts_iso))

    def last_sync(self) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM sync_state WHERE key = ?", (LAST_SYNC_KEY,)).fetchone()
        return row["value"] if row else None

    @staticmethod
    def _to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["all_day"] = bool(d["all_day"])
        d["attendees"] = json.loads(d["attendees"])
        return d
