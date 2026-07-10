"""NL event creation: fast-model extraction into a strict proposal, then a
mechanical validation gate. The gate — not the prompt — guarantees what reaches
the confirm dialog: parseable future times, sane duration, verbatim-validated
recurrence, and attendees only from addresses the user literally typed."""

import json
import re
from datetime import date, datetime, timedelta

MAX_DURATION_H = 12
DEFAULT_DURATION_MIN = 30
PAST_GRACE = timedelta(minutes=5)

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

SYSTEM_PROMPT = (
    "You turn the user's request into a calendar event proposal. Reply with ONLY "
    "a JSON object, no prose, shaped exactly:\n"
    '{"title": "...", "start": "YYYY-MM-DDTHH:MM", "end": "YYYY-MM-DDTHH:MM", '
    '"all_day": false, "location": null, "description": null, '
    '"attendees": [], "recurrence": null}\n'
    "Rules: times are local, 24h. all_day events use bare YYYY-MM-DD dates. "
    "attendees may ONLY contain email addresses the user explicitly wrote — a "
    "name is not an address; put names in the title instead. recurrence, if the "
    "user asked for one, is an RRULE string like \"RRULE:FREQ=WEEKLY;BYDAY=MO\". "
    "If no time was given, pick a sensible one (afternoon means 14:00)."
)


def parse_proposal(text: str) -> dict | None:
    """First balanced JSON object in the reply — models wrap JSON in prose."""
    start = (text or "").find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                        return obj if isinstance(obj, dict) else None
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return None


def _parse_local(value: str, tzinfo) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=tzinfo) if dt.tzinfo is None else dt


def validate_proposal(p: dict, *, now: datetime,
                      user_message: str) -> tuple[dict | None, str | None]:
    """Normalized proposal or (None, honest reason the user can act on)."""
    title = (p.get("title") or "").strip()
    if not title:
        return None, "I couldn't work out a title for the event — try rephrasing."

    all_day = bool(p.get("all_day"))
    if all_day:
        try:
            start_d = date.fromisoformat(str(p.get("start")))
            end_d = date.fromisoformat(str(p["end"])) if p.get("end") else start_d
        except (TypeError, ValueError):
            return None, "I couldn't pin down the date — say it like 'July 20'."
        if end_d < start_d:
            return None, "The end date is before the start date."
        if start_d < now.date():
            return None, "That date is in the past."
        start_s, end_s = start_d.isoformat(), end_d.isoformat()
    else:
        start = _parse_local(p.get("start"), now.tzinfo)
        if start is None:
            return None, "I couldn't pin down the time — say it like 'Friday 2pm'."
        end = _parse_local(p.get("end"), now.tzinfo) if p.get("end") else (
            start + timedelta(minutes=DEFAULT_DURATION_MIN))
        if end is None:
            return None, "I couldn't pin down the end time."
        if end <= start:
            return None, "The end time is before the start time."
        if end - start > timedelta(hours=MAX_DURATION_H):
            return None, (f"That's over {MAX_DURATION_H} hours long — that looks "
                          "wrong; give me a tighter time range (or say all day).")
        if start < now - PAST_GRACE:
            return None, "That time is in the past."
        start_s, end_s = start.isoformat(), end.isoformat()

    attendees = []
    msg_folded = (user_message or "").casefold()
    for a in p.get("attendees") or []:
        a = str(a).strip()
        if not EMAIL.match(a):
            return None, (f"I can only invite explicit email addresses, and "
                          f"{a!r} isn't one — say the address itself.")
        if a.casefold() not in msg_folded:
            return None, (f"I won't invite {a} — you didn't write that address "
                          "in your request, so I can't be sure it's right.")
        attendees.append(a)

    recurrence = (p.get("recurrence") or "").strip() or None
    if recurrence:
        from dateutil.rrule import rrulestr
        try:
            rrulestr(recurrence.removeprefix("RRULE:"))
        except (ValueError, TypeError, KeyError):
            return None, ("I couldn't turn that into a valid recurrence rule — "
                          "say it like 'every Monday'.")

    return {"title": title, "start": start_s, "end": end_s, "all_day": all_day,
            "location": (p.get("location") or "").strip() or None,
            "description": (p.get("description") or "").strip() or None,
            "attendees": attendees, "recurrence": recurrence}, None


def _when(p: dict) -> str:
    if p["all_day"]:
        s, e = date.fromisoformat(p["start"]), date.fromisoformat(p["end"])
        if s == e:
            return f"{s.strftime('%a %b %-d, %Y')} (all day)"
        return f"{s.strftime('%a %b %-d')} – {e.strftime('%a %b %-d, %Y')} (all day)"
    s, e = datetime.fromisoformat(p["start"]), datetime.fromisoformat(p["end"])
    return (f"{s.strftime('%a %b %-d, %Y')} · "
            f"{s.strftime('%H:%M')} – {e.strftime('%H:%M')}")


def _describe_rrule(rule: str) -> str:
    body = rule.removeprefix("RRULE:")
    parts = dict(kv.split("=", 1) for kv in body.split(";") if "=" in kv)
    desc = parts.get("FREQ", "").lower() or "custom"
    if parts.get("INTERVAL", "1") != "1":
        desc = f"every {parts['INTERVAL']} {desc.removesuffix('ly')}s"
    if parts.get("BYDAY"):
        desc += f" on {parts['BYDAY']}"
    return desc


def confirm_payload(p: dict) -> dict:
    """The exact dialog the user sees — recurrence verbatim, invites stated."""
    rows = [("Title", p["title"]), ("When", _when(p)),
            ("Calendar", "Personal (primary)")]
    if p["location"]:
        rows.append(("Location", p["location"]))
    if p["attendees"]:
        rows.append(("Attendees",
                     ", ".join(p["attendees"]) + " — an invite will be emailed"))
    if p["recurrence"]:
        rows.append(("Repeats", f"{_describe_rrule(p['recurrence'])} "
                                f"({p['recurrence']})"))
    return {"icon": "▲", "title": "Create calendar event",
            "intro": "Lumen will add this event to your Google Calendar.",
            "rows": rows, "confirm_label": "Create event"}


async def propose_event(llm, message: str, *, now: datetime,
                        model=None) -> tuple[dict | None, str | None]:
    """One structured generation on the fast model — no tools, no chain."""
    system = (f"{SYSTEM_PROMPT}\nNow: {now.strftime('%Y-%m-%d %H:%M')} "
              f"({now.strftime('%A')}), timezone "
              f"UTC{now.strftime('%z')[:3]}:{now.strftime('%z')[3:]}.")
    text = ""
    async for chunk in llm.chat([{"role": "system", "content": system},
                                 {"role": "user", "content": message}]):
        text += chunk
    raw = parse_proposal(text)
    if raw is None:
        return None, ("I couldn't turn that into an event — try including a "
                      "day and a time, like 'call with Sam Friday 2pm'.")
    return validate_proposal(raw, now=now, user_message=message)
