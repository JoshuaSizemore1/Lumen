"""Persistent per-connection enable/disable (#38).

A user can pause a connection (Gmail / Google Calendar / Canvas) from Settings
without removing its login; the pause has to survive a daemon restart, so it
lives in SQLite rather than in-memory config. An absent row means enabled, so
a fresh install has every connection on until the user turns one off.

The daemon is the only writer. The poll loops read `enabled(name)` each cycle
(via a `paused` predicate wired in __main__), so a toggle takes effect on the
next poll without restarting anything.
"""


class ConnectionState:
    NAMES = ("gmail", "google_calendar", "canvas")

    def __init__(self, conn):
        self._conn = conn

    def enabled(self, name: str) -> bool:
        row = self._conn.execute(
            "SELECT enabled FROM connection_state WHERE name = ?",
            (name,)).fetchone()
        return True if row is None else bool(row[0])

    def set_enabled(self, name: str, enabled: bool) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO connection_state (name, enabled) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET enabled = excluded.enabled",
                (name, 1 if enabled else 0))

    def all(self) -> dict:
        return {n: self.enabled(n) for n in self.NAMES}
