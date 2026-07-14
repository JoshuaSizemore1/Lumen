"""Free-slot finding: deterministic math over the calendar cache — working
hours, busy intervals, request parsing, and the rendered proposal text."""

from datetime import date, datetime

from lumen.daemon.connectors.free_slots import (SLOT_HEADER, find_slots,
                                                parse_duration, parse_window,
                                                render_slots)

NOW = datetime.fromisoformat("2026-07-13T09:10:00-06:00")   # Monday morning


def ev(start, end, **over):
    base = {"id": "e", "calendar_id": "primary", "title": "Busy",
            "start_at": start, "end_at": end, "all_day": False,
            "status": "confirmed", "attendees": []}
    base.update(over)
    return base


# ---- parse_duration ----

def test_duration_minutes_hours_and_default():
    assert parse_duration("find 30 minutes for a call") == 30
    assert parse_duration("find 45 min with sam") == 45
    assert parse_duration("find 2 hours this week") == 120
    assert parse_duration("find an hour tomorrow") == 60
    assert parse_duration("half an hour on friday") == 30
    assert parse_duration("find time for a call") == 30      # default


# ---- parse_window ----

def test_window_shapes():
    today = NOW.date()
    assert parse_window("find time today", NOW) == (today, today)
    assert parse_window("30 minutes tomorrow", NOW) == (date(2026, 7, 14),
                                                        date(2026, 7, 14))
    # Monday the 13th: "this week" runs through Sunday the 19th
    assert parse_window("a call this week", NOW) == (today, date(2026, 7, 19))
    assert parse_window("next week", NOW) == (date(2026, 7, 20), date(2026, 7, 26))
    assert parse_window("this weekend", NOW) == (date(2026, 7, 18), date(2026, 7, 19))
    assert parse_window("on wednesday", NOW) == (date(2026, 7, 15), date(2026, 7, 15))
    assert parse_window("find 30 minutes with sam", NOW) == (today, date(2026, 7, 20))


# ---- find_slots ----

def test_first_slot_today_starts_after_now_rounded():
    slots = find_slots([], duration_min=30, start_date=NOW.date(),
                       end_date=NOW.date(), now=NOW)
    # 09:10 now -> next half-hour boundary with lead is 09:30
    assert slots[0][0].isoformat() == "2026-07-13T09:30:00-06:00"


def test_future_day_starts_at_working_hours_and_weekends_count():
    sat = date(2026, 7, 18)
    slots = find_slots([], duration_min=30, start_date=sat, end_date=sat, now=NOW)
    assert slots[0][0].hour == 8                       # day_start default 08:00
    assert slots[0][0].date() == sat                   # Saturday is proposable


def test_busy_events_block_and_slots_avoid_them():
    busy = [ev("2026-07-14T08:00:00-06:00", "2026-07-14T12:00:00-06:00")]
    slots = find_slots(busy, duration_min=30, start_date=date(2026, 7, 14),
                       end_date=date(2026, 7, 14), now=NOW)
    assert slots[0][0].isoformat() == "2026-07-14T12:00:00-06:00"


def test_all_day_and_cancelled_events_do_not_block():
    rows = [ev("2026-07-14", "2026-07-15", all_day=True),
            ev("2026-07-14T08:00:00-06:00", "2026-07-14T20:00:00-06:00",
               status="cancelled")]
    slots = find_slots(rows, duration_min=30, start_date=date(2026, 7, 14),
                       end_date=date(2026, 7, 14), now=NOW)
    assert slots and slots[0][0].hour == 8


def test_slots_spread_across_days_before_repeating_a_day():
    slots = find_slots([], duration_min=30, start_date=date(2026, 7, 14),
                       end_date=date(2026, 7, 16), now=NOW, count=3)
    assert [s[0].date() for s in slots] == [date(2026, 7, 14), date(2026, 7, 15),
                                            date(2026, 7, 16)]


def test_fully_busy_window_yields_nothing():
    busy = [ev("2026-07-14T07:00:00-06:00", "2026-07-14T21:00:00-06:00")]
    slots = find_slots(busy, duration_min=30, start_date=date(2026, 7, 14),
                       end_date=date(2026, 7, 14), now=NOW)
    assert slots == []


def test_duration_respected_at_day_end():
    # 19:00-20:00 is the only gap; a 90-minute ask can't fit
    busy = [ev("2026-07-14T08:00:00-06:00", "2026-07-14T19:00:00-06:00")]
    assert find_slots(busy, duration_min=90, start_date=date(2026, 7, 14),
                      end_date=date(2026, 7, 14), now=NOW) == []
    hour = find_slots(busy, duration_min=60, start_date=date(2026, 7, 14),
                      end_date=date(2026, 7, 14), now=NOW)
    assert hour[0][0].hour == 19


def test_custom_working_hours():
    slots = find_slots([], duration_min=30, start_date=date(2026, 7, 14),
                       end_date=date(2026, 7, 14), now=NOW,
                       day_start="10:00", day_end="16:00")
    assert slots[0][0].hour == 10


# ---- render_slots ----

def test_render_numbers_options_with_explicit_dates():
    slots = find_slots([], duration_min=30, start_date=date(2026, 7, 14),
                       end_date=date(2026, 7, 16), now=NOW, count=3)
    text = render_slots(slots, 30)
    assert text.startswith(SLOT_HEADER)
    assert "1. Tue 2026-07-14 08:00–08:30" in text
    assert "2. Wed 2026-07-15" in text and "3. Thu 2026-07-16" in text
    assert "only" in text and "calendar" in text       # honesty note
    assert "book" in text.lower()                      # booking nudge
