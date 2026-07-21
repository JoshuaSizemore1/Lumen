"""Local mail-mirror MCP server: search_email / get_email over the SQLite
mirror — fast, offline, zero API quota. Read-only by design; mail writes are
UI-confirmed one-shots, never model-initiated. Run: python -m lumen.mcp_servers.mail"""

import sqlite3
from datetime import datetime

from mcp.server.fastmcp import FastMCP

from lumen.daemon.connectors.email_menu import EmailStore

mcp = FastMCP("mail")

BODY_MAX = 3500   # stay inside the router's TOOL_RESULT_MAX_CHARS


def _conn() -> sqlite3.Connection | None:
    from lumen.daemon.config import load_config
    path = load_config().db_path
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _local_dt(received_at: str) -> str:
    """UTC received_at → the local date/time the user actually saw it. Dates in
    results must match the user's clock, or 'on the 8th' answers look wrong."""
    try:
        return datetime.fromisoformat(received_at).astimezone().strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return (received_at or "")[:10]


def _search_email(conn, query: str, limit: int) -> str:
    rows = EmailStore(conn).search(query, limit=max(1, min(int(limit), 20)))
    if not rows:
        # Query-scoped, never "no email" — the mailbox has mail, this query just
        # didn't match. Steers the model to broaden rather than tell the user
        # they have no email (todo-fixes #11).
        return ("No email matched that query. Try fewer/broader terms, an empty "
                "query for the most recent messages, or on:/after: with a date.")
    return "\n".join(
        f"- id={r['id']} {_local_dt(r['received_at'])} {r['sender']} — "
        f"{r['subject']} :: {r['snippet']}" for r in rows)


def _get_email(conn, mid: str) -> str:
    r = EmailStore(conn).get(mid)
    if r is None:
        return "No email with that id."
    body = r["body"] or ""
    if len(body) > BODY_MAX:
        body = body[:BODY_MAX] + "\n[truncated — this is the first part of a longer message]"
    att = f"\nAttachments: {', '.join(r['attachments'])}" if r["attachments"] else ""
    return (f"From: {r['sender']}\nTo: {r['recipients']}\n"
            f"Date: {r['received_at']}\nSubject: {r['subject']}{att}\n\n{body}")


@mcp.tool()
def search_email(query: str = "", limit: int = 5) -> str:
    """Search the user's locally mirrored email, NEWEST FIRST. Returns message
    ids and summaries (dates shown in the user's local time); use get_email for
    a full message.

    Leave `query` empty to get the most recent messages. Filters combine in the
    query string; plain words search subject and body:
      - on:YYYY-MM-DD          messages received that day (the user's local day)
      - after:YYYY-MM-DD / before:YYYY-MM-DD   a date range
      - newer_than:7d          the last N days
      - from:name-or-email     sender (use in:sent for mail the user sent)
      - to:name-or-email       recipient
      - subject:words          words in the subject
      - is:unread
    Examples: `on:2026-07-08` (mail from that day); `from:chris invoice`;
    empty query (the latest mail). Do NOT quote-wrap the whole thing or use
    boolean operators. The mirror covers roughly the last 6 months."""
    conn = _conn()
    if conn is None:
        return "Email isn't synced yet."
    try:
        return _search_email(conn, query, limit)
    finally:
        conn.close()


@mcp.tool()
def get_email(id: str) -> str:
    """Fetch one mirrored email in full by the id search_email returned."""
    conn = _conn()
    if conn is None:
        return "Email isn't synced yet."
    try:
        return _get_email(conn, id)
    finally:
        conn.close()


if __name__ == "__main__":
    mcp.run()
