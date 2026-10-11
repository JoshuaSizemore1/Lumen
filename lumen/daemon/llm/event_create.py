"""NL event creation: fast-model extraction into a strict proposal, then a
mechanical validation gate. The gate — not the prompt — guarantees what reaches
the confirm dialog: parseable future times, sane duration, verbatim-validated
recurrence, and attendees only from addresses the user literally typed."""

import json
import re
from datetime import date, datetime, timedelta

from lumen.daemon.connectors.todo_parse import correct_year, resolve_relative_phrase

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


def _parse_local(value: str) -> datetime | None:
    """The proposal's wall-clock time as an aware local datetime, at that
    date's own UTC offset — not today's, which is an hour off across a DST
    change (#73). The prompt asks for local times, so an offset the model
    tacks on ("Z") is format noise: it was shown as local in the confirm but
    booked as UTC. The wall clock is what the user said."""
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=None).astimezone()


def _coerce_datetime_field(v) -> str | None:
    """Google Calendar returns start/end as {'dateTime': '...', 'timeZone': '...'}
    or {'date': '...'}; coerce to the bare string the gate expects (todo-fixes #119)."""
    if isinstance(v, dict):
        return v.get("dateTime") or v.get("date") or None
    return v


def _coerce_location(v) -> str | None:
    """Google Calendar returns location as a dict; extract its display text
    (todo-fixes #119). None if the dict has no usable string."""
    if isinstance(v, dict):
        return (v.get("displayName") or v.get("description")
                or v.get("text") or v.get("name") or None)
    return v


def _coerce_recurrence(v) -> tuple[str | None, str | None]:
    """Coerce a model-returned recurrence to (rrule_str_or_None, error_or_None).
    Handles Google's list format ['RRULE:...']; multiple/non-RRULE entries are
    rejected with a clear message (todo-fixes #119)."""
    if isinstance(v, list):
        rrules = [s for s in v if isinstance(s, str) and s.startswith("RRULE:")]
        non_rrules = [s for s in v if isinstance(s, str) and not s.startswith("RRULE:")]
        if len(rrules) == 1 and not non_rrules:
            return rrules[0], None
        if len(rrules) > 1 or non_rrules:
            return None, ("That recurrence has multiple or unsupported rules — "
                          "say it like 'every Monday' and I'll create one rule.")
        return None, None   # empty list
    if isinstance(v, dict):
        return None, ("I couldn't turn that recurrence into a rule — "
                      "say it like 'every Monday'.")
    return v, None


def validate_proposal(p: dict, *, now: datetime, user_message: str,
                      context: str = "") -> tuple[dict | None, str | None]:
    """Normalized proposal or (None, honest reason the user can act on).
    `context` is the daemon-authored slot-proposal block (plus the user's
    originating ask) for booking follow-ups — addresses typed there count
    as user-typed."""
    title = str(p.get("title") or "").strip()
    if not title:
        return None, "I couldn't work out a title for the event — try rephrasing."

    # Coerce Google-shaped start/end dicts before parsing (todo-fixes #119).
    start_raw = _coerce_datetime_field(p.get("start"))
    end_raw = _coerce_datetime_field(p.get("end"))

    all_day = bool(p.get("all_day"))
    if all_day:
        try:
            start_d = date.fromisoformat(str(start_raw))
            end_d = date.fromisoformat(str(end_raw)) if end_raw else start_d
        except (TypeError, ValueError):
            return None, "I couldn't pin down the date — say it like 'July 20'."
        if end_d < start_d:
            return None, "The end date is before the start date."
        if start_d < now.date():
            return None, "That date is in the past."
        start_s, end_s = start_d.isoformat(), end_d.isoformat()
    else:
        start = _parse_local(start_raw)
        if start is None:
            return None, "I couldn't pin down the time — say it like 'Friday 2pm'."
        end = _parse_local(end_raw) if end_raw else (
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
        # Google Calendar sends attendees as objects; extract the email (todo-fixes #119).
        if isinstance(a, dict):
            a = str(a.get("email") or "").strip()
        else:
            a = str(a).strip()
        if EMAIL.match(a) and a.casefold() in haystack:
            attendees.append(a)

    # Coerce recurrence (handles Google's list format; todo-fixes #119).
    raw_rec = p.get("recurrence")
    recurrence, rec_err = _coerce_recurrence(raw_rec)
    if rec_err:
        return None, rec_err
    recurrence = (recurrence or "").strip() or None
    if recurrence:
        from dateutil.rrule import rrulestr
        try:
            rrulestr(recurrence.removeprefix("RRULE:"))
        except (ValueError, TypeError, KeyError):
            return None, ("I couldn't turn that into a valid recurrence rule — "
                          "say it like 'every Monday'.")

    location = _coerce_location(p.get("location"))
    return {"title": title, "start": start_s, "end": end_s, "all_day": all_day,
            "location": (str(location or "")).strip() or None,
            "description": str(p.get("description") or "").strip() or None,
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


def apply_year_correction(raw: dict, text: str, today: date) -> dict:
    """Re-year the model's proposal from the user's own words when no exact
    relative date was resolvable ("March next year" pins a year, not a day).
    Shifts start and end by the same number of years so a span stays intact."""
    try:
        start = date.fromisoformat(str(raw.get("start") or "")[:10])
    except ValueError:
        return raw
    fixed = correct_year(start, text, today)
    if fixed.year == start.year:
        return raw
    shift = fixed.year - start.year
    out = dict(raw)
    for key in ("start", "end"):
        v = str(raw.get(key) or "")
        try:
            if len(v) == 10:
                d = date.fromisoformat(v)
                out[key] = d.replace(year=d.year + shift).isoformat()
            elif v:
                dt = datetime.fromisoformat(v)
                out[key] = dt.replace(year=dt.year + shift).isoformat(
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
    # Detect multi-date messages: after removing the resolved phrase, does
    # another resolvable date remain? If so, provide references for each but
    # skip the mechanical override — the model must pick the right one
    # (todo-fixes #118: "dinner tomorrow before the recital on friday").
    residual_resolved = None
    if resolved is not None:
        residual = re.sub(
            r"\b" + re.escape(resolved[1]) + r"\b", "",
            message, count=1, flags=re.IGNORECASE)
        residual = re.sub(r"\s+", " ", residual).strip()
        residual_resolved = resolve_relative_phrase(residual, now.date())
    # Two phrases naming the same day ("friday … this friday") are one date.
    multi_date = (residual_resolved is not None
                  and residual_resolved[0] != resolved[0])

    if resolved is not None and not multi_date:
        system += (f'\nIn this request, "{resolved[1]}" means '
                   f"{resolved[0].isoformat()} "
                   f"({resolved[0].strftime('%A')}) — use exactly this date.")
    elif multi_date:
        # Multiple date phrases — give each as a reference; the model decides
        # which one the event falls on (todo-fixes #118).
        refs = (f'"{resolved[1]}" = {resolved[0].isoformat()}'
                f' ({resolved[0].strftime("%A")}); '
                f'"{residual_resolved[1]}" = {residual_resolved[0].isoformat()}'
                f' ({residual_resolved[0].strftime("%A")})')
        system += f"\nDate reference for this request: {refs}."
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
    if resolved is not None and not multi_date:
        # Single resolved date — mechanically pin it (todo-fixes #8).
        raw = apply_resolved_date(raw, resolved[0])
    elif not context:
        # No pinned date (multi-date or no date) — still correct the year if
        # the words demand it (the 4B answers "March next year" with CURRENT year).
        raw = apply_year_correction(raw, message, now.date())
    return validate_proposal(raw, now=now, user_message=message,
                             context=context or "")
