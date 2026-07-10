"""Google Calendar: EventStore (local window cache the UI/chat read from) and
CalendarSync (rolling-window poll via the API client — never the LLM/MCP loop).
Store what the API returns; render local at the edges."""

import asyncio
import json
import logging
import sqlite3
from datetime import date, datetime, time, timedelta

from lumen.daemon.connectors import google_auth

log = logging.getLogger(__name__)

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


class CalendarSync:
    """Rolling-window pull of every non-hidden calendar into the EventStore.
    Plain API client on a timer — the poller must never wake the LLM."""

    def __init__(self, store: EventStore, google_cfg, sync_cfg, *, service_factory=None):
        self._store = store
        self._google = google_cfg
        self._sync = sync_cfg
        self._service_factory = service_factory or self._build_service

    @property
    def connected(self) -> bool:
        return google_auth.connected(self._google)

    def window(self, today: date | None = None) -> tuple[str, str]:
        today = today or date.today()
        return ((today - timedelta(days=self._sync.calendar_window_past_days)).isoformat(),
                (today + timedelta(days=self._sync.calendar_window_future_days)).isoformat())

    def _build_service(self):
        creds = google_auth.load_credentials(self._google, google_auth.READ_SCOPES)
        if creds is None:
            return None
        from googleapiclient.discovery import build
        return build("calendar", "v3", credentials=creds, cache_discovery=False)

    async def sync_once(self) -> bool:
        """True on a successful refresh; False keeps the stale cache untouched."""
        # API calls block in a worker thread; SQLite writes stay on the loop
        # thread (the connection — and the daemon's single-writer rule — is
        # thread-bound).
        fetched = await asyncio.to_thread(self._fetch_blocking)
        if fetched is None:
            return False
        events, start, end = fetched
        self._store.replace_window(events, start, end)
        self._store.set_last_sync(datetime.now().isoformat(timespec="seconds"))
        return True

    def _fetch_blocking(self) -> tuple[list[dict], str, str] | None:
        try:
            service = self._service_factory()
        except Exception:
            log.exception("could not build calendar service")
            return None
        if service is None:
            return None  # not connected yet — a normal state, checked every tick
        start, end = self.window()
        # aware local datetimes so the API window matches what the user calls a day
        time_min = datetime.combine(date.fromisoformat(start), time.min).astimezone()
        time_max = datetime.combine(date.fromisoformat(end), time.max).astimezone()
        events: list[dict] = []
        try:
            cals = service.calendarList().list().execute().get("items", [])
            for cal in cals:
                if cal.get("hidden") or cal.get("deleted"):
                    continue
                page_token = None
                while True:
                    resp = service.events().list(
                        calendarId=cal["id"], singleEvents=True,
                        timeMin=time_min.isoformat(), timeMax=time_max.isoformat(),
                        orderBy="startTime", maxResults=250,
                        pageToken=page_token).execute()
                    events.extend(self._normalize(item, cal)
                                  for item in resp.get("items", []))
                    page_token = resp.get("nextPageToken")
                    if not page_token:
                        break
        except Exception:
            log.exception("calendar sync failed — keeping stale cache")
            return None
        return events, start, end

    @staticmethod
    def _normalize(item: dict, cal: dict) -> dict:
        start, end = item.get("start", {}), item.get("end", {})
        return {
            "id": item.get("id"),
            "calendar_id": cal["id"],
            "calendar_name": cal.get("summary"),
            "color": cal.get("backgroundColor"),
            "title": item.get("summary"),
            "start_at": start.get("dateTime") or start.get("date"),
            "end_at": end.get("dateTime") or end.get("date"),
            "all_day": "date" in start,
            "location": item.get("location"),
            "description": item.get("description"),
            "attendees": [{"email": a.get("email"), "name": a.get("displayName"),
                           "self": a.get("self", False)}
                          for a in item.get("attendees", [])],
            "status": item.get("status"),
        }

    async def poll_forever(self) -> None:
        """Daemon background task; cancellation is the shutdown path."""
        interval = self._sync.calendar_poll_minutes * 60
        while True:
            try:
                await self.sync_once()
            except Exception:
                log.exception("calendar poll iteration failed")
            await asyncio.sleep(interval)
