"""EventStore window cache + CalendarSync normalization (fake API, no network)."""

import pytest

from lumen.daemon import db
from lumen.daemon.connectors.gcal import EventStore


@pytest.fixture(autouse=True)
def mountain_time(set_tz):
    """Events file under their LOCAL day (#72), so every expectation here
    depends on the zone — pin it rather than trust the machine's."""
    set_tz("America/Denver")


def make_store(tmp_path):
    return EventStore(db.connect(tmp_path / "e.db"))


def ev(id="e1", calendar_id="primary", title="Standup",
       start_at="2026-07-10T09:30:00+02:00", end_at="2026-07-10T10:00:00+02:00",
       all_day=False, **kw):
    base = {"id": id, "calendar_id": calendar_id, "calendar_name": "Personal",
            "color": "#7986cb", "title": title, "start_at": start_at,
            "end_at": end_at, "all_day": all_day, "location": None,
            "description": None, "attendees": [], "status": "confirmed"}
    base.update(kw)
    return base


def test_replace_window_clears_only_in_window_rows(tmp_path):
    store = make_store(tmp_path)
    store.replace_window([ev(id="old", start_at="2026-07-05T10:00:00+02:00"),
                          ev(id="out", start_at="2026-08-20T10:00:00+02:00")],
                         "2026-06-01", "2026-08-31")
    # second sync with a narrower window drops only rows inside that window
    store.replace_window([ev(id="new", start_at="2026-07-06T10:00:00+02:00")],
                         "2026-07-01", "2026-07-31")
    ids = {e["id"] for e in store.list_range("2026-01-01", "2026-12-31")}
    assert ids == {"new", "out"}


def test_list_range_inclusive_and_decodes(tmp_path):
    store = make_store(tmp_path)
    store.replace_window(
        [ev(id="a", start_at="2026-07-10T09:30:00+02:00",
            attendees=[{"email": "p@x.com", "name": "Priya", "self": False}]),
         ev(id="b", start_at="2026-07-11", end_at="2026-07-12", all_day=True),
         ev(id="c", start_at="2026-07-12T08:00:00+02:00")],
        "2026-07-01", "2026-07-31")
    rows = store.list_range("2026-07-10", "2026-07-11")
    assert [e["id"] for e in rows] == ["a", "b"]
    assert rows[0]["attendees"] == [{"email": "p@x.com", "name": "Priya", "self": False}]
    assert rows[0]["all_day"] is False and rows[1]["all_day"] is True


def test_list_range_orders_all_day_first_within_a_day(tmp_path):
    store = make_store(tmp_path)
    store.replace_window(
        [ev(id="timed", start_at="2026-07-11T08:00:00+02:00"),
         ev(id="allday", start_at="2026-07-11", all_day=True)],
        "2026-07-01", "2026-07-31")
    assert [e["id"] for e in store.list_range("2026-07-11", "2026-07-11")] == [
        "allday", "timed"]


def test_evening_event_lands_on_its_local_day(tmp_path, set_tz):
    """todo-fixes #72: SQLite's date() turns an offset stamp into UTC, which
    filed every event after 6 PM Mountain (5 PM after Nov 1) under tomorrow —
    missing from Today and shown on the wrong day. Canvas's 11:59 PM due-date
    events never appeared on their own day at all."""
    set_tz("America/Denver")
    store = make_store(tmp_path)
    store.replace_window(
        [ev(id="dinner", start_at="2026-10-11T19:00:00-06:00",
            end_at="2026-10-11T20:00:00-06:00"),
         ev(id="due", start_at="2026-11-05T23:44:00-07:00",
            end_at="2026-11-05T23:59:00-07:00"),
         # Google may hand back another zone's offset (an event on a
         # New York calendar): 9 PM there is still 7 PM here.
         ev(id="ny", start_at="2026-10-11T21:00:00-04:00",
            end_at="2026-10-11T22:00:00-04:00")],
        "2026-10-01", "2026-11-30")
    assert [e["id"] for e in store.list_range("2026-10-11", "2026-10-11")] == [
        "dinner", "ny"]
    assert store.list_range("2026-10-12", "2026-10-12") == []
    assert [e["id"] for e in store.list_range("2026-11-05", "2026-11-05")] == ["due"]
    assert "start_day" not in store.list_range("2026-10-11", "2026-10-11")[0]


def test_replace_window_drops_evening_rows_of_its_last_day(tmp_path, set_tz):
    """The window delete must use the same local day as the read, or an evening
    event on the window's last day survives every resync after it's deleted."""
    set_tz("America/Denver")
    store = make_store(tmp_path)
    store.replace_window([ev(id="gone", start_at="2026-10-11T19:00:00-06:00")],
                         "2026-10-01", "2026-10-11")
    store.replace_window([], "2026-10-01", "2026-10-11")
    assert store.list_range("2026-01-01", "2026-12-31") == []


def test_migration_backfills_local_day_on_an_existing_cache(tmp_path, set_tz):
    """A cache written before start_day existed must read right on first start,
    not only after the next sync."""
    import sqlite3
    set_tz("America/Denver")
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.executescript("""
        CREATE TABLE events (
            id TEXT NOT NULL, calendar_id TEXT NOT NULL, calendar_name TEXT,
            color TEXT, event_color TEXT, title TEXT, start_at TEXT NOT NULL,
            end_at TEXT, all_day INTEGER NOT NULL DEFAULT 0, location TEXT,
            description TEXT, attendees TEXT NOT NULL DEFAULT '[]', status TEXT,
            PRIMARY KEY (calendar_id, id));
        INSERT INTO events (id, calendar_id, title, start_at, all_day)
            VALUES ('dinner', 'primary', 'Dinner', '2026-10-11T19:00:00-06:00', 0),
                   ('pto', 'primary', 'PTO', '2026-10-12', 1);
    """)
    raw.commit()
    raw.close()
    store = EventStore(db.connect(path))
    assert [e["id"] for e in store.list_range("2026-10-11", "2026-10-11")] == ["dinner"]
    assert [e["id"] for e in store.list_range("2026-10-12", "2026-10-12")] == ["pto"]


def test_a_null_local_day_is_healed_on_the_next_start(tmp_path):
    """A crash between the ALTER and the backfill left NULL days, which no
    later start used to revisit — those events stayed invisible."""
    path = tmp_path / "e.db"
    conn = db.connect(path)
    EventStore(conn).replace_window([ev(id="dinner",
                                        start_at="2026-10-11T19:00:00-06:00")],
                                    "2026-10-01", "2026-10-31")
    with conn:
        conn.execute("UPDATE events SET start_day = NULL")
    conn.close()
    store = EventStore(db.connect(path))
    assert [e["id"] for e in store.list_range("2026-10-11", "2026-10-11")] == ["dinner"]


def test_get_by_calendar_and_id(tmp_path):
    store = make_store(tmp_path)
    store.replace_window([ev(id="e1"), ev(id="e1", calendar_id="work")],
                         "2026-07-01", "2026-07-31")
    row = store.get("work", "e1")
    assert row["calendar_id"] == "work" and row["all_day"] is False
    assert row["attendees"] == []
    assert store.get("primary", "ghost") is None


def test_last_sync_roundtrip(tmp_path):
    store = make_store(tmp_path)
    assert store.last_sync() is None
    store.set_last_sync("2026-07-10T14:00:00")
    store.set_last_sync("2026-07-10T14:05:00")
    assert store.last_sync() == "2026-07-10T14:05:00"


# ---- CalendarSync (fake service objects, no network) ----

from types import SimpleNamespace

from lumen.daemon.config import GoogleConfig, SyncConfig
from lumen.daemon.connectors.gcal import CalendarSync


class FakeExec:
    def __init__(self, payload):
        self._p = payload

    def execute(self):
        if isinstance(self._p, Exception):
            raise self._p
        return self._p


class FakeEventsAPI:
    def __init__(self, pages_by_cal):
        self._pages = pages_by_cal
        self.seen_kwargs = []

    def list(self, calendarId, pageToken=None, **kw):
        self.seen_kwargs.append({"calendarId": calendarId, "pageToken": pageToken, **kw})
        pages = self._pages[calendarId]
        return FakeExec(pages[0 if pageToken is None else int(pageToken)])


class FakeService:
    def __init__(self, cals, pages_by_cal):
        self._cals = cals
        self.events_api = FakeEventsAPI(pages_by_cal)

    def calendarList(self):
        return SimpleNamespace(list=lambda: FakeExec({"items": self._cals}))

    def events(self):
        return self.events_api


CALS = [
    {"id": "primary", "summary": "Personal", "backgroundColor": "#7986cb"},
    {"id": "work@group", "summary": "Work", "backgroundColor": "#f6bf26"},
    {"id": "spam", "summary": "Hidden one", "hidden": True},
]

TIMED = {"id": "t1", "summary": "Standup",
         "start": {"dateTime": "2026-07-10T09:30:00+02:00"},
         "end": {"dateTime": "2026-07-10T10:00:00+02:00"},
         "status": "confirmed", "location": "Meet",
         "attendees": [{"email": "p@x.com", "displayName": "Priya", "self": False}]}
ALLDAY = {"id": "a1", "summary": "PTO",
          "start": {"date": "2026-07-20"}, "end": {"date": "2026-07-21"},
          "status": "confirmed"}


def make_sync(tmp_path, service, connected=True):
    store = make_store(tmp_path)
    factory = (lambda: service) if connected else (lambda: None)
    sync = CalendarSync(store, GoogleConfig(client_secret_path=tmp_path / "cs",
                                            token_path=tmp_path / "tok"),
                        SyncConfig(), service_factory=factory)
    return store, sync


async def test_sync_once_merges_calendars_skips_hidden_and_normalizes(tmp_path):
    service = FakeService(CALS, {"primary": [{"items": [TIMED]}],
                                 "work@group": [{"items": [ALLDAY]}]})
    store, sync = make_sync(tmp_path, service)
    assert await sync.sync_once() is True
    rows = store.list_range("2026-01-01", "2026-12-31")
    assert {r["id"] for r in rows} == {"t1", "a1"}
    timed = next(r for r in rows if r["id"] == "t1")
    assert timed["calendar_id"] == "primary" and timed["calendar_name"] == "Personal"
    assert timed["color"] == "#7986cb" and timed["all_day"] is False
    assert timed["start_at"] == "2026-07-10T09:30:00+02:00"
    assert timed["attendees"] == [{"email": "p@x.com", "name": "Priya", "self": False}]
    allday = next(r for r in rows if r["id"] == "a1")
    assert allday["all_day"] is True and allday["start_at"] == "2026-07-20"
    assert store.last_sync() is not None
    # hidden calendar never queried
    assert all(k["calendarId"] != "spam" for k in service.events_api.seen_kwargs)
    # singleEvents so recurring events arrive expanded
    assert all(k["singleEvents"] is True for k in service.events_api.seen_kwargs)


async def test_sync_once_follows_pagination(tmp_path):
    pages = {"primary": [{"items": [TIMED], "nextPageToken": "1"},
                         {"items": [ALLDAY]}]}
    service = FakeService([CALS[0]], pages)
    store, sync = make_sync(tmp_path, service)
    assert await sync.sync_once() is True
    assert len(store.list_range("2026-01-01", "2026-12-31")) == 2


async def test_sync_once_not_connected_is_a_quiet_no_op(tmp_path):
    store, sync = make_sync(tmp_path, None, connected=False)
    assert await sync.sync_once() is False
    assert store.list_range("2026-01-01", "2026-12-31") == []
    assert store.last_sync() is None


async def test_sync_once_api_error_keeps_stale_cache(tmp_path):
    good = FakeService([CALS[0]], {"primary": [{"items": [TIMED]}]})
    store, sync = make_sync(tmp_path, good)
    await sync.sync_once()
    before = store.last_sync()
    bad = FakeService([CALS[0]], {"primary": [RuntimeError("boom")]})
    sync2 = CalendarSync(store, GoogleConfig(client_secret_path=tmp_path / "cs",
                                             token_path=tmp_path / "tok"),
                         SyncConfig(), service_factory=lambda: bad)
    assert await sync2.sync_once() is False
    assert [r["id"] for r in store.list_range("2026-01-01", "2026-12-31")] == ["t1"]
    assert store.last_sync() == before


# --- #59: manual refresh means two callers can now sync at once --------------
async def test_overlapping_syncs_collapse_into_one_fetch(tmp_path):
    """Until #59 only the 5-minute poll tick ever called sync_once, so it could
    never race itself. Now a Canvas write and a user pressing ↻ can both land
    inside one poll tick, and pulling the whole window twice is pure cost on a
    laptop that has none to spare — so the second caller rides the first."""
    import asyncio

    service = FakeService([CALS[0]], {"primary": [{"items": [TIMED]}]})
    store, sync = make_sync(tmp_path, service)
    results = await asyncio.gather(sync.sync_once(), sync.sync_once())
    assert results == [True, True]                 # both callers see a success
    assert len(service.events_api.seen_kwargs) == 1
    assert [r["id"] for r in store.list_range("2026-01-01", "2026-12-31")] == ["t1"]


async def test_busy_reports_a_sync_in_flight(tmp_path):
    service = FakeService([CALS[0]], {"primary": [{"items": [TIMED]}]})
    _store, sync = make_sync(tmp_path, service)
    assert sync.busy is False
    await sync.sync_once()
    assert sync.busy is False
