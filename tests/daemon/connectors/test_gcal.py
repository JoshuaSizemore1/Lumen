"""EventStore window cache + CalendarSync normalization (fake API, no network)."""

from lumen.daemon import db
from lumen.daemon.connectors.gcal import EventStore


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


def test_last_sync_roundtrip(tmp_path):
    store = make_store(tmp_path)
    assert store.last_sync() is None
    store.set_last_sync("2026-07-10T14:00:00")
    store.set_last_sync("2026-07-10T14:05:00")
    assert store.last_sync() == "2026-07-10T14:05:00"
