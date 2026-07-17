"""NL event creation: fast-model extraction into a strict proposal, then a
mechanical validation gate. The gate — not the prompt — guarantees what reaches
the confirm dialog: parseable future times, sane duration, verbatim-validated
recurrence, and attendees only from addresses the user literally typed."""

import json
import re
from datetime import date, datetime, timedelta

from lumen.daemon.connectors.todo_parse import resolve_relative_phrase

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


def validate_proposal(p: dict, *, now: datetime, user_message: str,
                      context: str = "") -> tuple[dict | None, str | None]:
    """Normalized proposal or (None, honest reason the user can act on).
    `context` is the daemon-authored slot-proposal block (plus the user's
    originating ask) for booking follow-ups — addresses typed there count
    as user-typed."""
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

    # Drop, don't refuse: an invite may only go to an address the user
    # literally typed. Names and invented addresses (the 4B reliably guesses
    # one for "call with Chris" — live 2026-07-14) fall out of the attendee
    # list instead of sinking the whole event; the confirm dialog then shows
    # no invite, so what will happen stays fully visible.
    attendees = []
    haystack = f"{user_message or ''}\n{context or ''}".casefold()
    for a in p.get("attendees") or []:
        a = str(a).strip()
        if EMAIL.match(a) and a.casefold() in haystack:
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


def apply_resolved_date(raw: dict, target: date) -> dict:
    """Move the model's proposal onto `target`'s date, keeping its times and
    duration — code, not the small model, owns relative-date arithmetic
    (todo-fixes #8). Unparseable values pass through for the gate to reject."""
    try:
        delta = target - date.fromisoformat(str(raw.get("start") or "")[:10])
    except ValueError:
        return raw
    if not delta:
        return raw
    out = dict(raw)
    for key in ("start", "end"):
        v = str(raw.get(key) or "")
        try:
            if len(v) == 10:
                out[key] = (date.fromisoformat(v) + delta).isoformat()
            elif v:
                out[key] = (datetime.fromisoformat(v) + delta).isoformat(
                    timespec="minutes")
        except ValueError:
            pass
    return out


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


async def propose_event(llm, message: str, *, now: datetime, model=None,
                        context: str | None = None) -> tuple[dict | None, str | None]:
    """One structured generation on the fast model — no tools, no chain.
    `context` carries a just-proposed slot list so "book the first one"
    resolves to an exact time; the validation gate applies unchanged."""
    system = (f"{SYSTEM_PROMPT}\nNow: {now.strftime('%Y-%m-%d %H:%M')} "
              f"({now.strftime('%A')}), timezone "
              f"UTC{now.strftime('%z')[:3]}:{now.strftime('%z')[3:]}.")
    # A booking follow-up's `context` carries exact slot dates — never
    # second-guess those; otherwise resolve the relative date in code and
    # hand the model the concrete answer (todo-fixes #8).
    resolved = None if context else resolve_relative_phrase(message, now.date())
    if resolved is not None:
        system += (f'\nIn this request, "{resolved[1]}" means '
                   f"{resolved[0].isoformat()} "
                   f"({resolved[0].strftime('%A')}) — use exactly this date.")
    if context:
        system += ("\nYou just proposed these times to the user; they are "
                   f"choosing one of them:\n{context}\n"
                   "Remember: names of people go in the title only — never "
                   "invent an email address for the attendees list.")
    text = ""
    async for chunk in llm.chat([{"role": "system", "content": system},
                                 {"role": "user", "content": message}]):
        text += chunk
    raw = parse_proposal(text)
    if raw is None:
        return None, ("I couldn't turn that into an event — try including a "
                      "day and a time, like 'call with Sam Friday 2pm'.")
    if resolved is not None:
        raw = apply_resolved_date(raw, resolved[0])
    return validate_proposal(raw, now=now, user_message=message,
                             context=context or "")
