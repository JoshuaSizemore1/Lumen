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


def _status(exc) -> int | None:
    """HTTP status off a googleapiclient HttpError, without importing it."""
    resp = getattr(exc, "resp", None)
    status = getattr(resp, "status", None) or getattr(exc, "status_code", None)
    try:
        return int(status)
    except (TypeError, ValueError):
        return None

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

    def get(self, calendar_id: str, event_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM events WHERE calendar_id = ? AND id = ?",
            (calendar_id, event_id)).fetchone()
        return self._to_dict(row) if row is not None else None

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
        # Runtime Disable switch (#38); __main__ wires it to ConnectionState.
        self.paused = lambda: False
        # #59 gave this two more callers than the poll tick — the manual refresh
        # route and the Canvas write's re-read — so it can now race itself. A
        # second caller arriving mid-sync rides the running fetch instead of
        # pulling the whole window down again.
        self._inflight: asyncio.Task | None = None

    @property
    def connected(self) -> bool:
        return google_auth.connected(self._google)

    # Facade the router consumes — one object for cache reads + sync status.
    def list_range(self, start_iso: str, end_iso: str) -> list[dict]:
        return self._store.list_range(start_iso, end_iso)

    def get(self, calendar_id: str, event_id: str) -> dict | None:
        return self._store.get(calendar_id, event_id)

    def last_sync(self) -> str | None:
        return self._store.last_sync()

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

    @property
    def busy(self) -> bool:
        return self._inflight is not None and not self._inflight.done()

    async def sync_once(self) -> bool:
        """True on a successful refresh; False keeps the stale cache untouched.

        Coalesced: a caller arriving mid-sync awaits the fetch already running
        rather than starting a second one — its answer is the fresh window that
        caller wanted anyway. A lock would not do: `Lock.acquire` yields even
        uncontended, so both callers get past a `locked()` check before either
        holds it. Sharing the task decides who fetches with no await in between.

        Shielded so one caller going away — an IPC client that disconnected —
        cannot cancel the refresh the other callers are still waiting on."""
        if self._inflight is None or self._inflight.done():
            self._inflight = asyncio.create_task(self._fetch_and_store())
        return await asyncio.shield(self._inflight)

    async def _fetch_and_store(self) -> bool:
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
            await self._poll_iteration()
            await asyncio.sleep(interval)

    async def _poll_iteration(self) -> None:
        if self.paused():                # runtime Disable switch (#38)
            return
        try:
            await self.sync_once()
        except Exception:
            log.exception("calendar poll iteration failed")


LUMEN_TAG = "canvas"          # extendedProperties.private.lumen


def local_tz_name() -> str | None:
    """The machine's IANA zone name. Google interprets a bare dateTime against
    the CALENDAR's default zone, so an event whose offset disagrees with that
    default silently lands at the wrong hour. Sending the zone explicitly is what
    makes "15 minutes before the due time" mean it."""
    import os
    try:
        real = os.path.realpath("/etc/localtime")
        marker = "/zoneinfo/"
        if marker in real:
            return real.split(marker, 1)[1]
    except OSError:
        pass
    return os.environ.get("TZ") or None


def canvas_event_body(title: str, start: str, end: str, *, kind: str = "timed",
                      description: str = "", color_id: str | None = None,
                      html_url: str | None = None,
                      assignment_id: int | None = None,
                      tz_name: str | None = None) -> dict:
    """The Google event body for one Canvas due date. A module-level pure builder
    so body-shape tests need no fake service at all.

    extendedProperties.private is the safety net: it marks an event as Lumen's,
    which is what guarantees a future cleanup pass can never delete something the
    user created by hand."""
    body: dict = {"summary": title}
    if kind == "all_day":
        body["start"] = {"date": start}
        body["end"] = {"date": end}
    else:
        tz = tz_name if tz_name is not None else local_tz_name()
        body["start"] = {"dateTime": start}
        body["end"] = {"dateTime": end}
        if tz:
            body["start"]["timeZone"] = tz
            body["end"]["timeZone"] = tz
    if description:
        body["description"] = description
    if color_id:
        body["colorId"] = str(color_id)
    if html_url:
        body["source"] = {"title": "Canvas", "url": html_url}
    # The event IS the reminder — a default popup an hour ahead would fire before
    # the 15-minute window even opens.
    body["reminders"] = {"useDefault": False,
                         "overrides": [{"method": "popup", "minutes": 0}]}
    private = {"lumen": LUMEN_TAG}
    if assignment_id is not None:
        private["assignment_id"] = str(assignment_id)
    body["extendedProperties"] = {"private": private}
    return body


class CalendarMarkerWriter:
    """Confirmation-gated writer for thin all-day Canvas due markers on the
    user's primary calendar. Separate from the read-only CalendarSync poller:
    the daemon only calls this AFTER the UI's batch confirm resolves. Returns
    the event id so reconciliation can later move/track the marker."""

    def __init__(self, google_cfg, *, service_factory=None):
        self._cfg = google_cfg
        self._service_factory = service_factory or self._build_service

    def _build_service(self):
        creds = google_auth.load_credentials(self._cfg, google_auth.WRITE_SCOPES)
        if creds is None:
            return None
        from googleapiclient.discovery import build
        return build("calendar", "v3", credentials=creds, cache_discovery=False)

    @staticmethod
    def _span(day: str) -> dict:
        end_excl = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
        return {"start": {"date": day}, "end": {"date": end_excl}}

    # --- generic event writes (Canvas -> Calendar) ---
    def create_event(self, body: dict) -> str | None:
        service = self._service_factory()
        if service is None:
            return None
        try:
            created = service.events().insert(
                calendarId="primary", body=body, sendUpdates="none").execute()
        except Exception:
            log.exception("canvas event insert failed")
            return None
        return created.get("id")

    def patch_event(self, event_id: str, body: dict) -> bool:
        """False on failure. A 404 here means the user deleted the event by hand;
        the caller demotes to create rather than retrying forever."""
        service = self._service_factory()
        if service is None:
            return False
        try:
            service.events().patch(
                calendarId="primary", eventId=event_id, body=body,
                sendUpdates="none").execute()
        except Exception as exc:
            if _status(exc) == 404:
                log.info("canvas event %s is gone — will recreate", event_id)
            else:
                log.exception("canvas event patch failed")
            return False
        return True

    def delete_event(self, event_id: str) -> bool:
        """Already-absent counts as success: 404/410 mean the calendar is in the
        state we wanted, and treating them as failure wedges the queue on a row
        that can never be resolved."""
        service = self._service_factory()
        if service is None:
            return False
        try:
            service.events().delete(
                calendarId="primary", eventId=event_id,
                sendUpdates="none").execute()
        except Exception as exc:
            if _status(exc) in (404, 410):
                return True
            log.exception("canvas event delete failed")
            return False
        return True

    # --- the original all-day markers, now thin delegates ---
    def create_all_day(self, title: str, day: str) -> str | None:
        return self.create_event({"summary": title, **self._span(day)})

    def patch_all_day(self, event_id: str, day: str) -> bool:
        return self.patch_event(event_id, self._span(day))
