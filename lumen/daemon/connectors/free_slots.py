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

# todo-fixes #121: fractional hours ("1.5 hours", "2.5 hrs") — check before
# the plain integer hour pattern, which matches the partial digit "5" in "1.5"
_FRAC_HOURS = re.compile(r"\b(\d+\.\d+)\s*(?:hours?|hrs?)\b", re.IGNORECASE)
# "1 hour 30 minutes" / "1 hour and 30 minutes"
_HOURS_MINS = re.compile(
    r"\b(\d+)\s*(?:hours?|hrs?)\s+(?:and\s+)?(\d+)\s*(?:min(?:ute)?s?)\b",
    re.IGNORECASE)
_MINUTES = re.compile(r"\b(\d{1,3})\s*(?:min(?:ute)?s?)\b", re.IGNORECASE)
_HOURS = re.compile(r"\b(\d{1,2})\s*(?:hours?|hrs?)\b", re.IGNORECASE)
_AN_HOUR = re.compile(r"\ban?\s+hour\b", re.IGNORECASE)
# "an hour and a half" / "a hour and a half"
_HOUR_HALF = re.compile(r"\ban?\s+hour\s+and\s+a\s+half\b", re.IGNORECASE)
_HALF_HOUR = re.compile(r"\bhalf\s+(?:an\s+)?hour\b", re.IGNORECASE)

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday",
             "saturday", "sunday")


_LENGTHS = (
    (_HOURS_MINS, lambda m: int(m.group(1)) * 60 + int(m.group(2))),
    (_HOUR_HALF, lambda m: 90),
    (_HALF_HOUR, lambda m: 30),
    (_FRAC_HOURS, lambda m: round(float(m.group(1)) * 60)),
    (_HOURS, lambda m: int(m.group(1)) * 60),
    (_MINUTES, lambda m: int(m.group(1))),
    (_AN_HOUR, lambda m: 60),
)


def parse_duration(message: str) -> int:
    """The first length the message names (#121). Leftmost wins, not the
    first pattern: "a 30 minute slot in the next 2 hours" is 30. At the same
    spot the longest reading wins: "1 hour 30 minutes" over "1 hour", "an
    hour and a half" over "an hour"; and "1.5 hours" starts before the "5
    hours" inside it."""
    found = [(m.start(), -len(m.group(0)), minutes(m))
             for pat, minutes in _LENGTHS if (m := pat.search(message))]
    return min(found)[2] if found else DEFAULT_DURATION_MIN


def parse_window(message: str, now: datetime) -> tuple[date, date]:
    """(start_date, end_date) the request names; default is the next week."""
    low = message.lower()
    today = now.date()
    wd = today.weekday()            # 0=Mon … 5=Sat, 6=Sun
    # "weekend" must come before "next week" / "this week" (it contains "week")
    if re.search(r"\bweekend\b", low):
        # "this weekend" semantics: on Sat → Sat+Sun, on Sun → Sun only,
        # Mon–Fri → the coming Sat+Sun (todo-fixes #121).
        # "next weekend" = the full Sat+Sun AFTER "this weekend".
        if re.search(r"\bnext\s+weekend\b", low):
            if wd == 5:             # Saturday → next Sat+Sun
                sat = today + timedelta(days=7)
            elif wd == 6:           # Sunday → next Sat is 6 days away
                sat = today + timedelta(days=6)
            else:                   # Mon–Fri → Sat after next
                sat = today + timedelta(days=(5 - wd) + 7)
            return sat, sat + timedelta(days=1)
        # "this weekend"
        if wd == 6:                 # Sunday — the weekend is almost done
            return today, today     # only today remains
        if wd == 5:                 # Saturday — full weekend ahead
            return today, today + timedelta(days=1)
        # Mon–Fri — upcoming Sat+Sun
        sat = today + timedelta(days=(5 - wd))
        return sat, sat + timedelta(days=1)
    if "next week" in low:
        start = today + timedelta(days=7 - wd)
        return start, start + timedelta(days=6)
    if "this week" in low:
        return today, today + timedelta(days=6 - wd)
    if "tomorrow" in low:
        return today + timedelta(days=1), today + timedelta(days=1)
    if "today" in low:
        return today, today
    for i, name in enumerate(_WEEKDAYS):
        if re.search(rf"\b{name}\b", low):
            target = today + timedelta(days=(i - wd) % 7 or 7)
            return target, target
    return today, today + timedelta(days=DEFAULT_WINDOW_DAYS)


def _hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def _busy_intervals(events: list[dict]) -> list[tuple[datetime, datetime]]:
    """Timed, non-cancelled events as aware local intervals. All-day rows
    (holidays, PTO markers) don't block — they aren't appointments. Declined
    invites and transparent ('show as free') events also don't block
    (todo-fixes #125)."""
    out = []
    for e in events:
        if e.get("all_day") or e.get("status") == "cancelled":
            continue
        if e.get("response_status") == "declined":
            continue
        if e.get("transparency") == "transparent":
            continue
        try:
            start = datetime.fromisoformat(e["start_at"]).astimezone()
            end = (datetime.fromisoformat(e["end_at"]).astimezone()
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
    busy = _busy_intervals(events)
    duration = timedelta(minutes=duration_min)
    step = timedelta(minutes=STEP_MIN)
    per_day: list[list[tuple[datetime, datetime]]] = []
    day = start_date
    while day <= end_date:
        # Each day's own offset, not now's: across a DST change today's
        # offset puts "08:00" an hour off on the far side (#73).
        window_start = datetime.combine(day, _hhmm(day_start)).astimezone()
        window_end = datetime.combine(day, _hhmm(day_end)).astimezone()
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
