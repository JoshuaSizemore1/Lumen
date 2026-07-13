"""Google Calendar MCP server: list_events for what the local cache can't
answer, plus create_event / delete_event — which the daemon only ever calls
after the user approved the exact event in a confirm dialog. Shares the
poller's OAuth token via google_auth. Run: python -m lumen.mcp_servers.gcal"""

from datetime import date, datetime, time, timedelta

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("gcal")

NOT_CONNECTED = ("Google Calendar isn't connected yet — the user needs to run "
                 "'uv run lumen-google-auth' first (see docs/google-oauth-setup.md).")
FAILED = "Couldn't reach Google Calendar right now."


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


def _list_events(service, start: str, end: str) -> str:
    try:
        s, e = date.fromisoformat(start), date.fromisoformat(end)
    except (TypeError, ValueError):
        return "start and end must be ISO dates like 2026-07-14."
    if service is None:
        return NOT_CONNECTED
    time_min = datetime.combine(s, time.min).astimezone().isoformat()
    time_max = datetime.combine(e, time.max).astimezone().isoformat()
    lines: list[str] = []
    try:
        cals = service.calendarList().list().execute().get("items", [])
        for cal in cals:
            if cal.get("hidden") or cal.get("deleted"):
                continue
            page_token = None
            while True:
                resp = service.events().list(
                    calendarId=cal["id"], singleEvents=True, orderBy="startTime",
                    timeMin=time_min, timeMax=time_max, maxResults=100,
                    pageToken=page_token).execute()
                lines.extend(_format_event(item, cal.get("summary", cal["id"]))
                             for item in resp.get("items", []))
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break
    except Exception:
        return FAILED
    if not lines:
        return f"No events between {start} and {end}."
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
    try:
        created = service.events().insert(
            calendarId="primary", body=body,
            sendUpdates="all" if attendees else "none").execute()
    except Exception:
        return FAILED
    link = created.get("htmlLink", "")
    return (f"Created: {created.get('summary', title)} — {start}"
            + (f" ({link})" if link else ""))


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
    """List the user's Google Calendar events between two ISO dates (inclusive),
    e.g. start='2026-09-01' end='2026-09-30'. Use this only for dates the
    assistant's calendar context doesn't already cover."""
    return _list_events(_service(), start, end)


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
