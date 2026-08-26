"""The two Canvas→Calendar switches, stored in the existing sync_state KV rather
than a table of their own — two booleans do not earn a schema (modelled on
connection_state.py, which reads the same way).

BOTH default OFF. This feature writes to the user's real primary calendar, so an
absent row must never mean "start syncing"; merging the code can't surprise
anyone, and the first write only ever follows an explicit toggle."""

SYNC_KEY = "canvas_calendar_sync"
AI_KEY = "canvas_calendar_ai"


class CanvasPrefs:
    def __init__(self, conn):
        self._conn = conn

    def _get(self, key: str) -> bool:
        row = self._conn.execute(
            "SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
        return False if row is None else row[0] == "1"

    def _set(self, key: str, on: bool) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO sync_state (key, value) VALUES (?, ?)",
                (key, "1" if on else "0"))

    def sync_enabled(self) -> bool:
        return self._get(SYNC_KEY)

    def set_sync_enabled(self, on: bool) -> None:
        self._set(SYNC_KEY, bool(on))

    def ai_mode(self) -> bool:
        """The 'Lumen powered' switch. Only meaningful while sync is on."""
        return self._get(AI_KEY)

    def set_ai_mode(self, on: bool) -> None:
        self._set(AI_KEY, bool(on))

    def all(self) -> dict:
        return {"sync": self.sync_enabled(), "ai": self.ai_mode()}
