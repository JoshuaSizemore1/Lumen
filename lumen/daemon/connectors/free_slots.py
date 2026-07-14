"""Free-slot proposals from the user's own calendar cache. All math is
deterministic daemon code — the rendered options carry exact dates/times so
a follow-up "book the first one" can hand them to the event-creation flow.
Lumen sees only the user's calendars: "with Sam" means "when *you* are free"."""

import re
from datetime import date, datetime, time, timedelta

SLOT_HEADER = "Free times on your calendar"

DEFAULT_DURATION_MIN = 30
DEFAULT_WINDOW_DAYS = 7
DAY_START = "08:00"     # user decision: 8:00–20:00 local, weekends included
DAY_END = "20:00"
STEP_MIN = 30
LEAD_MIN = 15           # today's first option is never sooner than this
DEFAULT_COUNT = 3

_MINUTES = re.compile(r"\b(\d{1,3})\s*(?:min(?:ute)?s?)\b", re.IGNORECASE)
_HOURS = re.compile(r"\b(\d{1,2})\s*(?:hours?|hrs?)\b", re.IGNORECASE)
_AN_HOUR = re.compile(r"\ban?\s+hour\b", re.IGNORECASE)
_HALF_HOUR = re.compile(r"\bhalf\s+(?:an\s+)?hour\b", re.IGNORECASE)

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday",
             "saturday", "sunday")


def parse_duration(message: str) -> int:
    if m := _MINUTES.search(message):
        return int(m.group(1))
    if _HALF_HOUR.search(message):
        return 30
    if m := _HOURS.search(message):
        return int(m.group(1)) * 60
    if _AN_HOUR.search(message):
        return 60
    return DEFAULT_DURATION_MIN


def parse_window(message: str, now: datetime) -> tuple[date, date]:
    """(start_date, end_date) the request names; default is the next week."""
    low = message.lower()
    today = now.date()
    # weekend first: "this weekend" contains the substring "this week"
    if re.search(r"\bweekend\b", low):
        sat = today + timedelta(days=(5 - today.weekday()) % 7)
        return sat, sat + timedelta(days=1)
    if "next week" in low:
        start = today + timedelta(days=7 - today.weekday())
        return start, start + timedelta(days=6)
    if "this week" in low:
        return today, today + timedelta(days=6 - today.weekday())
    if "tomorrow" in low:
        return today + timedelta(days=1), today + timedelta(days=1)
    if "today" in low:
        return today, today
    for i, name in enumerate(_WEEKDAYS):
        if re.search(rf"\b{name}\b", low):
            target = today + timedelta(days=(i - today.weekday()) % 7 or 7)
            return target, target
    return today, today + timedelta(days=DEFAULT_WINDOW_DAYS)


def _hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def _busy_intervals(events: list[dict], tzinfo) -> list[tuple[datetime, datetime]]:
    """Timed, non-cancelled events as aware local intervals. All-day rows
    (holidays, PTO markers) don't block — they aren't appointments."""
    out = []
    for e in events:
        if e.get("all_day") or e.get("status") == "cancelled":
            continue
        try:
            start = datetime.fromisoformat(e["start_at"]).astimezone(tzinfo)
            end = (datetime.fromisoformat(e["end_at"]).astimezone(tzinfo)
                   if e.get("end_at") else start + timedelta(minutes=30))
        except (TypeError, ValueError):
            continue
        out.append((start, end))
    return sorted(out)


def find_slots(events: list[dict], *, duration_min: int, start_date: date,
               end_date: date, now: datetime, day_start: str = DAY_START,
               day_end: str = DAY_END, count: int = DEFAULT_COUNT
               ) -> list[tuple[datetime, datetime]]:
    """Up to `count` open slots, spread across days before repeating one:
    half-hour-aligned starts inside working hours that overlap no busy event."""
    busy = _busy_intervals(events, now.tzinfo)
    duration = timedelta(minutes=duration_min)
    step = timedelta(minutes=STEP_MIN)
    per_day: list[list[tuple[datetime, datetime]]] = []
    day = start_date
    while day <= end_date:
        window_start = datetime.combine(day, _hhmm(day_start), tzinfo=now.tzinfo)
        window_end = datetime.combine(day, _hhmm(day_end), tzinfo=now.tzinfo)
        if day == now.date():
            earliest = now + timedelta(minutes=LEAD_MIN)
            minute = (earliest.minute // STEP_MIN + 1) * STEP_MIN
            earliest = earliest.replace(minute=0, second=0, microsecond=0) \
                + timedelta(minutes=minute)
            window_start = max(window_start, earliest)
        slots, cursor = [], window_start
        while cursor + duration <= window_end and len(slots) < count:
            if not any(s < cursor + duration and cursor < e for s, e in busy):
                slots.append((cursor, cursor + duration))
            cursor += step
        if slots:
            per_day.append(slots)
        day += timedelta(days=1)
    out: list[tuple[datetime, datetime]] = []
    rank = 0
    while len(out) < count and any(rank < len(d) for d in per_day):
        for d in per_day:
            if rank < len(d) and len(out) < count:
                out.append(d[rank])
        rank += 1
    return out


def render_slots(slots: list[tuple[datetime, datetime]], duration_min: int) -> str:
    """Deterministic proposal — exact dates/times, numbered so "book the
    first one" is unambiguous, honest about seeing only the user's calendar."""
    lines = [f"{SLOT_HEADER} ({duration_min} min):"]
    for i, (s, e) in enumerate(slots, 1):
        lines.append(f"{i}. {s.strftime('%a')} {s.date().isoformat()} "
                     f"{s.strftime('%H:%M')}–{e.strftime('%H:%M')}")
    lines.append("")
    lines.append("These are based only on your own calendar — I can't see "
                 "anyone else's availability. Say e.g. \"book the first one\" "
                 "and I'll set it up.")
    return "\n".join(lines)
