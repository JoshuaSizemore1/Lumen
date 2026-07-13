"""Local mail-mirror MCP server: search_email / get_email over the SQLite
mirror — fast, offline, zero API quota. Read-only by design; mail writes are
UI-confirmed one-shots, never model-initiated. Run: python -m lumen.mcp_servers.mail"""

import sqlite3

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


def _search_email(conn, query: str, limit: int) -> str:
    rows = EmailStore(conn).search(query, limit=max(1, min(int(limit), 20)))
    if not rows:
        return "No matching email."
    return "\n".join(
        f"- id={r['id']} {r['received_at'][:10]} {r['sender']} — "
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
def search_email(query: str, limit: int = 5) -> str:
    """Search the user's locally mirrored email (subject, body, sender).
    Returns matching message ids and summaries; use get_email for a full
    message. The mirror covers roughly the last 6 months."""
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
