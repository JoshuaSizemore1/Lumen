"""Local-time helpers for calendar events: the UTC offset stamped onto an
edited event's times, and the local day an event belongs to. Its own module,
like gcal_colors, so both UIs share one copy without importing the router."""
from datetime import date, datetime, timedelta


def local_day(start_at: str) -> str:
    """The local date (YYYY-MM-DD) an event starts on — the day Today and the
    day view file it under. SQLite's date() converts an offset stamp to UTC,
    which put every evening event on tomorrow (todo-fixes #72). An all-day
    event's bare date is already a local day."""
    text = str(start_at or "")
    if len(text) == 10:
        return text
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return text[:10]
    return (dt.astimezone() if dt.tzinfo else dt).date().isoformat()


def local_end_day(end_at: str | None, all_day: bool) -> str | None:
    """The last local date the event occupies (todo-fixes #113).

    All-day: Google's end date is EXCLUSIVE ("2026-07-11" for a one-day PTO on
    the 10th), so subtract one day. Timed: convert to local; an end exactly at
    midnight has no presence on that new day, so it belongs to the previous one
    (e.g. a 10 PM–midnight party ends on the same day it started)."""
    if not end_at:
        return None
    text = str(end_at)
    if all_day:
        if len(text) == 10:
            try:
                return (date.fromisoformat(text) - timedelta(days=1)).isoformat()
            except ValueError:
                return None
        return None
    if len(text) == 10:
        return text
    try:
        dt = datetime.fromisoformat(text)
        local = dt.astimezone() if dt.tzinfo else dt
        # End at exact midnight = start of the next day; the event has no
        # presence on that day, so the last day is the one before.
        if local.hour == 0 and local.minute == 0 and local.second == 0:
            return (local.date() - timedelta(days=1)).isoformat()
        return local.date().isoformat()
    except ValueError:
        return text[:10]


def tz_offset(day: date | None = None, h: int = 12, m: int = 0) -> str:
    """The local UTC offset (+HH:MM) on that day at that time — not today's:
    an edit to an event across a DST change would otherwise move it an hour."""
    when = datetime.combine(day or date.today(),
                            datetime.min.time().replace(hour=h, minute=m))
    off = when.astimezone().strftime("%z")   # e.g. -0600
    return f"{off[:3]}:{off[3:]}" if off else "+00:00"
