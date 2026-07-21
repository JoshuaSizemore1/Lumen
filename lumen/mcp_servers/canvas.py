"""Local Canvas-mirror MCP server: list_assignments / get_assignment /
list_announcements over the SQLite mirror — fast, offline, zero Canvas API
quota. Read-only by design (Lumen never writes to Canvas; assignment todos and
calendar markers are handled by the daemon, not the model).
Run: python -m lumen.mcp_servers.canvas"""

import sqlite3
from datetime import datetime

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("canvas")

DESC_MAX = 1500


def _conn() -> sqlite3.Connection | None:
    from lumen.daemon.config import load_config
    path = load_config().db_path
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _local_day(utc_iso: str | None) -> str:
    if not utc_iso:
        return "no due date"
    try:
        return datetime.fromisoformat(utc_iso).astimezone().strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return utc_iso[:10]


def _list_assignments(conn, course: str, limit: int) -> str:
    limit = max(1, min(int(limit), 50))
    rows = conn.execute(
        "SELECT a.*, c.name AS cname, c.course_code AS ccode "
        "FROM canvas_assignments a JOIN canvas_courses c ON c.id = a.course_id "
        "WHERE c.active = 1 ORDER BY a.due_at IS NULL, a.due_at, a.id").fetchall()
    needle = (course or "").strip().lower()
    if needle:
        rows = [r for r in rows
                if needle in (r["ccode"] or "").lower()
                or needle in (r["cname"] or "").lower()]
    rows = rows[:limit]
    if not rows:
        return ("No assignments matched. Canvas may be between terms, or the "
                "course filter was too narrow — try an empty course filter.")
    out = []
    for r in rows:
        label = r["ccode"] or r["cname"] or "?"
        state = " (submitted)" if r["submitted"] else ""
        out.append(f"- id={r['id']} due {_local_day(r['due_at'])} — {label}: "
                   f"{r['name']}{state}")
    return "\n".join(out)


def _get_assignment(conn, aid: str) -> str:
    try:
        key = int(aid)
    except (TypeError, ValueError):
        return "No assignment with that id."
    r = conn.execute(
        "SELECT a.*, c.name AS cname, c.course_code AS ccode "
        "FROM canvas_assignments a JOIN canvas_courses c ON c.id = a.course_id "
        "WHERE a.id = ?", (key,)).fetchone()
    if r is None:
        return "No assignment with that id."
    label = r["ccode"] or r["cname"] or "?"
    pts = f"\nPoints: {r['points']}" if r["points"] is not None else ""
    link = f"\nLink: {r['html_url']}" if r["html_url"] else ""
    desc = (r["description"] or "").strip()
    if len(desc) > DESC_MAX:
        desc = desc[:DESC_MAX] + "\n[truncated]"
    body = f"\n\n{desc}" if desc else ""
    submitted = "yes" if r["submitted"] else "no"
    return (f"{label}: {r['name']}\nDue: {_local_day(r['due_at'])}\n"
            f"Submitted: {submitted}{pts}{link}{body}")


def _list_announcements(conn, limit: int) -> str:
    limit = max(1, min(int(limit), 30))
    rows = conn.execute(
        "SELECT n.*, c.name AS cname, c.course_code AS ccode "
        "FROM canvas_announcements n JOIN canvas_courses c ON c.id = n.course_id "
        "WHERE c.active = 1 ORDER BY n.posted_at DESC, n.id DESC "
        "LIMIT ?", (limit,)).fetchall()
    if not rows:
        return "No course announcements in the mirror yet."
    out = []
    for r in rows:
        label = r["ccode"] or r["cname"] or "?"
        snippet = (r["message"] or "").strip().replace("\n", " ")[:160]
        out.append(f"- {_local_day(r['posted_at'])} {label}: {r['title']} :: {snippet}")
    return "\n".join(out)


@mcp.tool()
def list_assignments(course: str = "", limit: int = 20) -> str:
    """List the user's known Canvas assignments (active courses only), soonest
    due first. Optionally filter by a course code or name substring, e.g.
    course='CS3505'. Access is already set up — never decline for credentials.
    Returns ids for get_assignment. If empty, Canvas may be between terms."""
    conn = _conn()
    if conn is None:
        return "Canvas isn't synced yet."
    try:
        return _list_assignments(conn, course, limit)
    finally:
        conn.close()


@mcp.tool()
def get_assignment(id: str) -> str:
    """Fetch one Canvas assignment in full by the id list_assignments returned —
    course, due date, points, link, and description."""
    conn = _conn()
    if conn is None:
        return "Canvas isn't synced yet."
    try:
        return _get_assignment(conn, id)
    finally:
        conn.close()


@mcp.tool()
def list_announcements(limit: int = 10) -> str:
    """List recent Canvas course announcements (active courses only), newest
    first, with the course, date, and a snippet. Use for 'any new announcements?'
    Access is already set up — never decline for credentials."""
    conn = _conn()
    if conn is None:
        return "Canvas isn't synced yet."
    try:
        return _list_announcements(conn, limit)
    finally:
        conn.close()


if __name__ == "__main__":
    mcp.run()
