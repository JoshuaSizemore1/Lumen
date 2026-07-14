"""Japanese-study nudge: read Manabi's last-review signal from its own
SQLite DB, read-only. Zero SRS logic lives here — one MAX(timestamp)
compared to today; doing the reviews in Manabi clears it on the next read."""

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# Either counts as "studied Japanese today": SRS card reviews or kana drills.
_QUERIES = ("SELECT MAX(reviewed) FROM reviews",
            "SELECT MAX(started) FROM kana_sessions")


class ManabiStatus:
    def __init__(self, db_path: Path | None):
        self._db = Path(db_path) if db_path else None

    def _read(self) -> str | None:
        """Latest study timestamp as an aware ISO string (Manabi stores
        sqlite datetime('now'), which is UTC), or None for no rows."""
        uri = f"file:{self._db}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            vals = []
            for q in _QUERIES:
                try:
                    v = conn.execute(q).fetchone()[0]
                except sqlite3.OperationalError:
                    continue           # table missing — fine, check the other
                if v:
                    vals.append(v)
        finally:
            conn.close()
        if not vals:
            return None
        dt = datetime.fromisoformat(max(vals))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()

    def status(self, now: datetime | None = None) -> dict:
        """{"configured", "due", "last_review"} — due is None (never nag)
        whenever the signal is missing or unreadable."""
        now = now or datetime.now().astimezone()
        if self._db is None or not self._db.exists():
            return {"configured": False, "due": None, "last_review": None}
        try:
            last = self._read()
        except sqlite3.Error:
            log.exception("could not read Manabi DB at %s", self._db)
            return {"configured": False, "due": None, "last_review": None}
        due = (last is None
               or datetime.fromisoformat(last).astimezone(now.tzinfo).date()
               < now.date())
        return {"configured": True, "due": due, "last_review": last}
