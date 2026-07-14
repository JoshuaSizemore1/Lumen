"""Meeting prep: find the event in the calendar cache, pull each attendee's
recent mail from the local mirror, and narrate once. Retrieval is
deterministic daemon-side — the model summarizes what it was handed, with
subjects/dates so every claim is checkable (book-recs grounding pattern)."""

import re
from datetime import datetime

SYSTEM = (
    "You are Lumen, the user's local assistant. Compose a short meeting brief "
    "from the data below: what the meeting is, who is in it, and what the "
    "recent correspondence with each attendee was about. Mention only what is "
    "shown, and cite the email subjects and dates given so the user can check "
    "them. If an attendee has no email history or a section is unavailable, "
    "say that plainly and move on. No filler, no invented detail, no sign-off."
)

MAILS_PER_ATTENDEE = 5

_TIME = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\b"
                   r"|\b(\d{1,2}):(\d{2})\b", re.IGNORECASE)

# words that shape the request, not the meeting
_STOP = frozenset(
    "prep prepare me for my our the a an this that these meeting meetings "
    "call calls event events appointment appointments sync with on at in "
    "today tomorrow next week am pm and of to about".split())


def _times_wanted(message: str) -> list[tuple[int, int | None]]:
    """(hour, minute-or-None) readings of any clock time in the message; a
    bare '9:30' with no am/pm yields both the 09:30 and 21:30 readings."""
    out = []
    for m in _TIME.finditer(message):
        if m.group(3):                       # 2pm / 2:30 pm
            h = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "p" else 0)
            out.append((h, int(m.group(2)) if m.group(2) else None))
        else:                                # bare 9:30 — ambiguous half-day
            h, minute = int(m.group(4)), int(m.group(5))
            out.append((h, minute))
            out.append(((h + 12) % 24, minute))
    return out


def _words(text: str) -> set[str]:
    return set(re.findall(r"[\w']+", (text or "").lower()))


def _event_words(e: dict) -> set[str]:
    words = _words(e.get("title") or "")
    for a in e.get("attendees") or []:
        words |= _words(a.get("name") or "")
        email = a.get("email") or ""
        words |= _words(email.split("@")[0])
    return words


def _start(e: dict, now: datetime) -> datetime:
    s = e["start_at"]
    if e.get("all_day"):
        return datetime.fromisoformat(s[:10] + "T00:00:00").replace(tzinfo=now.tzinfo)
    return datetime.fromisoformat(s).astimezone(now.tzinfo)


def find_event(events: list[dict], message: str, now: datetime) -> dict | None:
    """Deterministic fuzzy lookup: clock times filter, title/attendee word
    overlap ranks, the soonest upcoming candidate wins. None means 'I don't
    see that meeting' — never a guess."""
    times = _times_wanted(message)
    query = _words(message) - _STOP
    candidates = []
    for e in events:
        start = _start(e, now)
        if times and not e.get("all_day"):
            if not any(start.hour == h and (mi is None or start.minute == mi)
                       for h, mi in times):
                continue
        elif times:
            continue                      # a clock time never means an all-day event
        score = len(query & _event_words(e))
        if not times and score == 0:
            continue
        candidates.append((score, start, e))
    if not candidates:
        return None
    best = max(s for s, _, _ in candidates)
    tied = [(start, e) for s, start, e in candidates if s == best]
    upcoming = sorted((c for c in tied if c[0] >= now), key=lambda c: c[0])
    if upcoming:
        return upcoming[0][1]
    return max(tied, key=lambda c: c[0])[1]


def _when(e: dict, now: datetime) -> str:
    if e.get("all_day"):
        s = _start(e, now)
        return f"{s.strftime('%a')} {s.date().isoformat()} (all day)"
    s = datetime.fromisoformat(e["start_at"]).astimezone(now.tzinfo)
    when = f"{s.strftime('%a')} {s.date().isoformat()} {s.strftime('%H:%M')}"
    if e.get("end_at"):
        when += f"–{datetime.fromisoformat(e['end_at']).astimezone(now.tzinfo).strftime('%H:%M')}"
    return when


def build_prep_data(event: dict, history: dict[str, list[dict]] | None,
                    now: datetime) -> str:
    """The exact data block the model narrates: the event, then each
    attendee's recent messages (date, direction, subject) with explicit
    no-history / mirror-unavailable markers."""
    loc = f" at {event['location']}" if event.get("location") else ""
    cal = f" [{event['calendar_name']}]" if event.get("calendar_name") else ""
    lines = [f"MEETING: {event.get('title') or 'Untitled'} — "
             f"{_when(event, now)}{loc}{cal}"]
    if event.get("description"):
        lines.append(f"Description: {event['description'][:500]}")
    attendees = [a for a in event.get("attendees") or [] if not a.get("self")]
    lines.append("")
    lines.append("ATTENDEES (recent email with each, from the local mirror):")
    if not attendees:
        lines.append("No attendees listed on this event.")
        return "\n".join(lines)
    if history is None:
        lines.append("Email mirror unavailable — no correspondence shown.")
    for a in attendees:
        who = f"{a['name']} <{a['email']}>" if a.get("name") else a.get("email", "?")
        if history is None:
            lines.append(f"- {who}")
            continue
        rows = history.get(a.get("email") or "", [])
        if not rows:
            lines.append(f"- {who}: no email history in the mirror.")
            continue
        lines.append(f"- {who}:")
        addr = (a.get("email") or "").lower()
        for m in rows:
            day = (m.get("received_at") or "")[:10]
            them = addr and addr in (m.get("sender") or "").lower()
            direction = "from them" if them else "from you"
            lines.append(f"  - {day}: {direction} — “{m.get('subject') or '(no subject)'}”")
    return "\n".join(lines)


async def compose_prep(llm, data: str):
    """One streaming narration pass on the fast model."""
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"Prep me for this meeting:\n\n{data}"}]
    async for chunk in llm.chat(messages):
        yield chunk
