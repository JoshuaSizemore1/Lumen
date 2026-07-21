"""Google Calendar MCP server: list_events (a date range) and search_events (a
topic, when the date is the unknown) for what the local cache can't answer,
plus create_event / delete_event — which the daemon only ever calls after the
user approved the exact event in a confirm dialog. Shares the poller's OAuth
token via google_auth. Run: python -m lumen.mcp_servers.gcal"""

import os
from datetime import date, datetime, time, timedelta

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("gcal")

NOT_CONNECTED = ("Google Calendar isn't connected yet — the user needs to run "
                 "'uv run lumen-google-auth' first (see docs/google-oauth-setup.md).")
FAILED = "Couldn't reach Google Calendar right now."


def _local_tz_name() -> str | None:
    """The machine's IANA time-zone name (e.g. 'America/Denver').

    Google Calendar REQUIRES an IANA timeZone on a recurring event's start/end;
    a UTC offset baked into the dateTime is not enough, and without it the
    insert fails outright — which is exactly why recurring events "wouldn't
    connect" (todo-fixes #16). Derived from /etc/localtime, the reliable source
    on the Linux laptop Lumen targets; TZ is the fallback."""
    try:
        real = os.path.realpath("/etc/localtime")
        marker = "/zoneinfo/"
        if marker in real:
            return real.split(marker, 1)[1]
    except OSError:
        pass
    return os.environ.get("TZ") or None


def _service(scopes=None):
    from lumen.daemon.config import load_config
    from lumen.daemon.connectors import google_auth
    creds = google_auth.load_credentials(load_config().google,
                                         scopes or google_auth.READ_SCOPES)
    if creds is None:
        return None
    from googleapiclient.discovery import build
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _format_event(item: dict, calendar_name: str) -> str:
    start, end = item.get("start", {}), item.get("end", {})
    if "date" in start:
        when = f"{start['date']} (all day)"
    else:
        s = datetime.fromisoformat(start["dateTime"]).astimezone()
        when = f"{s.strftime('%Y-%m-%d %H:%M')}"
        if end.get("dateTime"):
            when += f"–{datetime.fromisoformat(end['dateTime']).astimezone().strftime('%H:%M')}"
    loc = f" at {item['location']}" if item.get("location") else ""
    return f"- {when}: {item.get('summary', 'Untitled')} [{calendar_name}]{loc}"


def _collect(service, time_min: str, time_max: str, q: str | None) -> list[str]:
    """Every visible calendar's events in the window, optionally filtered by
    Google's own full-text search (`q` covers title, description, location and
    attendees)."""
    lines: list[str] = []
    cals = service.calendarList().list().execute().get("items", [])
    for cal in cals:
        if cal.get("hidden") or cal.get("deleted"):
            continue
        page_token = None
        while True:
            resp = service.events().list(
                calendarId=cal["id"], singleEvents=True, orderBy="startTime",
                timeMin=time_min, timeMax=time_max, maxResults=100,
                pageToken=page_token, **({"q": q} if q else {})).execute()
            lines.extend(_format_event(item, cal.get("summary", cal["id"]))
                         for item in resp.get("items", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    return lines


def _list_events(service, start: str, end: str) -> str:
    try:
        s, e = date.fromisoformat(start), date.fromisoformat(end)
    except (TypeError, ValueError):
        return "start and end must be ISO dates like 2026-07-14."
    if service is None:
        return NOT_CONNECTED
    try:
        lines = _collect(service,
                         datetime.combine(s, time.min).astimezone().isoformat(),
                         datetime.combine(e, time.max).astimezone().isoformat(),
                         None)
    except Exception:
        return FAILED
    if not lines:
        return f"No events between {start} and {end}."
    return "\n".join(sorted(lines))


def _search_events(service, query: str, months_back: int, months_ahead: int,
                   today: date | None = None) -> str:
    query = (query or "").strip()
    if not query:
        return "query is required — the word or phrase to look for."
    if service is None:
        return NOT_CONNECTED
    # Clamped so a model that passes something wild can't ask Google for a
    # century of events.
    months_back = max(0, min(int(months_back), 60))
    months_ahead = max(0, min(int(months_ahead), 60))
    today = today or date.today()
    s = today - timedelta(days=31 * months_back)
    e = today + timedelta(days=31 * months_ahead)
    try:
        lines = _collect(service,
                         datetime.combine(s, time.min).astimezone().isoformat(),
                         datetime.combine(e, time.max).astimezone().isoformat(),
                         query)
    except Exception:
        return FAILED
    if not lines:
        # Phrased as a completed search, not as missing information: the model
        # must be able to tell the user "you have nothing matching that"
        # without reaching for "I don't have access".
        return (f"Searched the user's whole calendar from {s.isoformat()} to "
                f"{e.isoformat()} and found no event matching {query!r}. "
                f"The calendar was read successfully — there is simply no such "
                f"event in that range.")
    return "\n".join(sorted(lines))


def _create_event(service, title: str, start: str, end: str, all_day: bool,
                  location: str, description: str, attendees: list[str],
                  recurrence: str) -> str:
    if service is None:
        return NOT_CONNECTED
    body: dict = {"summary": title}
    if location:
        body["location"] = location
    if description:
        body["description"] = description
    if all_day:
        # Google all-day ends are exclusive; our proposals carry inclusive dates
        end_excl = (date.fromisoformat(end) + timedelta(days=1)).isoformat()
        body["start"], body["end"] = {"date": start}, {"date": end_excl}
    else:
        body["start"], body["end"] = {"dateTime": start}, {"dateTime": end}
    if attendees:
        body["attendees"] = [{"email": a} for a in attendees]
    if recurrence:
        rule = recurrence if recurrence.startswith("RRULE") else f"RRULE:{recurrence}"
        body["recurrence"] = [rule]
        # Recurring timed events need an IANA timeZone or Google rejects the
        # insert (#16). All-day recurring events use bare dates, which are fine.
        if not all_day:
            tzname = _local_tz_name()
            if tzname:
                body["start"]["timeZone"] = tzname
                body["end"]["timeZone"] = tzname
    try:
        created = service.events().insert(
            calendarId="primary", body=body,
            sendUpdates="all" if attendees else "none").execute()
    except Exception:
        return FAILED
    link = created.get("htmlLink", "")
    return (f"Created: {created.get('summary', title)} — {start}"
            + (f" ({link})" if link else ""))


def _update_event(service, event_id: str, calendar_id: str, title: str,
                  start: str, end: str, all_day: bool, location: str,
                  description: str, color_id: str) -> str:
    """Patch an existing event (#12). Only non-empty fields are sent, so a
    caller can change just the colour or just the time without clobbering the
    rest. Google's colour is a colorId ('1'..'11'); '' leaves it unchanged."""
    if not event_id:
        return "event_id is required."
    if service is None:
        return NOT_CONNECTED
    body: dict = {}
    if title:
        body["summary"] = title
    if location:
        body["location"] = location
    if description:
        body["description"] = description
    if color_id:
        body["colorId"] = color_id
    if start and end:
        if all_day:
            end_excl = (date.fromisoformat(end) + timedelta(days=1)).isoformat()
            body["start"], body["end"] = {"date": start}, {"date": end_excl}
        else:
            body["start"], body["end"] = ({"dateTime": start},
                                          {"dateTime": end})
    if not body:
        return "Nothing to change."
    try:
        updated = service.events().patch(
            calendarId=calendar_id or "primary", eventId=event_id,
            body=body, sendUpdates="none").execute()
    except Exception:
        return FAILED
    return f"Updated: {updated.get('summary', title or event_id)}"


def _delete_event(service, event_id: str, calendar_id: str,
                  notify_attendees: bool) -> str:
    if not event_id:
        return "event_id is required."
    if service is None:
        return NOT_CONNECTED
    try:
        service.events().delete(
            calendarId=calendar_id or "primary", eventId=event_id,
            sendUpdates="all" if notify_attendees else "none").execute()
    except Exception:
        return FAILED
    return f"Deleted: {event_id}"


@mcp.tool()
def list_events(start: str, end: str) -> str:
    """List everything on the user's own calendar between two ISO dates
    (inclusive), e.g. start='2026-09-01' end='2026-09-30'. Access is already
    set up and handled for you — this reads the user's own data, so never
    decline for lack of permission, credentials, or account access.

    Use this when the user names a period and wants to see what is in it
    ('what's on next week?', 'am I busy in September?'). Call it for ANY date
    range, including dates months or years out and dates in the past. The
    calendar listing in your system message covers only a short window around
    today; when the user asks about a date outside that window, call this tool
    with that range instead of telling them the event isn't shown.

    If you know WHAT the user is asking about but not WHEN, use search_events
    instead — do not scan a wide range with this tool."""
    return _list_events(_service(), start, end)


@mcp.tool()
def search_events(query: str, months_back: int = 1, months_ahead: int = 12) -> str:
    """Find an event on the user's own calendar by what it is about, when you
    do not know its date. Searches title, description, location and attendees
    across every one of the user's calendars. Access is already set up and
    handled for you — this reads the user's own data, so never decline for lack
    of permission, credentials, or account access.

    This is the right tool for 'when is my next dentist appointment?', 'when is
    the parent-teacher conference?', 'do I have therapy this month?', 'when am I
    seeing the lawyer?'. Pass the distinguishing word as `query` — 'dentist',
    'parent-teacher', 'therapy'. The defaults already reach a year ahead and a
    month back, which covers the ordinary question; widen them for the distant
    past or the far future.

    Every one of those is an ordinary entry on the user's own calendar, and
    looking one up for them is exactly what you are for. What an event is about
    never makes it off-limits. An event missing from the listing in your system
    message has NOT been ruled out — it was merely outside that short window.
    Search here before ever saying you don't know when something is."""
    return _search_events(_service(), query, months_back, months_ahead)


@mcp.tool()
def create_event(title: str, start: str, end: str, all_day: bool = False,
                 location: str = "", description: str = "",
                 attendees: list[str] = [], recurrence: str = "") -> str:
    """Create an event on the user's primary Google Calendar. The daemon calls
    this only after the user explicitly confirmed the exact details in a dialog
    — never call it speculatively."""
    from lumen.daemon.connectors import google_auth
    return _create_event(_service(google_auth.WRITE_SCOPES), title, start, end,
                         all_day, location, description, list(attendees),
                         recurrence)


@mcp.tool()
def update_event(event_id: str, calendar_id: str = "primary", title: str = "",
                 start: str = "", end: str = "", all_day: bool = False,
                 location: str = "", description: str = "",
                 color_id: str = "") -> str:
    """Edit an existing event on the user's Google Calendar — change its title,
    time, location, description, or colour. Only the fields you pass are
    changed. The daemon calls this only after the user confirmed the exact
    change in a dialog — never call it speculatively."""
    from lumen.daemon.connectors import google_auth
    return _update_event(_service(google_auth.WRITE_SCOPES), event_id,
                         calendar_id, title, start, end, all_day, location,
                         description, color_id)


@mcp.tool()
def delete_event(event_id: str, calendar_id: str = "primary",
                 notify_attendees: bool = False) -> str:
    """Permanently delete an event from the user's Google Calendar. The daemon
    calls this only after the user explicitly confirmed the exact event in a
    dialog — never call it speculatively."""
    from lumen.daemon.connectors import google_auth
    return _delete_event(_service(google_auth.WRITE_SCOPES), event_id,
                         calendar_id, notify_attendees)


if __name__ == "__main__":
    mcp.run()
