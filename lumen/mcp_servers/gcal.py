"""Google Calendar MCP server — the LLM's live window for what the local cache
can't answer (events beyond the sync window). Shares the poller's OAuth token via
google_auth; read-only until the Phase 5 write half adds create_event.
Run: python -m lumen.mcp_servers.gcal"""

from datetime import date, datetime, time

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


@mcp.tool()
def list_events(start: str, end: str) -> str:
    """List the user's Google Calendar events between two ISO dates (inclusive),
    e.g. start='2026-09-01' end='2026-09-30'. Use this only for dates the
    assistant's calendar context doesn't already cover."""
    return _list_events(_service(), start, end)


if __name__ == "__main__":
    mcp.run()
