"""Thin read-only Open Library MCP server (no API key). The reusable 'custom MCP
server' template for the project — Phase 4's book catalog and later lookups follow
this shape. Run: python -m lumen.mcp_servers.openlibrary"""

import httpx
from mcp.server.fastmcp import FastMCP

BASE_URL = "https://openlibrary.org"
TIMEOUT = httpx.Timeout(connect=5.0, read=8.0, write=5.0, pool=5.0)

mcp = FastMCP("openlibrary")


async def _search(client: httpx.AsyncClient, query: str, limit: int) -> str:
    try:
        resp = await client.get("/search.json",
                                params={"q": query, "limit": limit,
                                        "fields": "title,author_name,first_publish_year,key,isbn"})
        resp.raise_for_status()
        docs = resp.json().get("docs", [])
    except (httpx.HTTPError, ValueError):
        return "Couldn't look up books right now (Open Library request failed)."
    if not docs:
        return f"No results for {query!r}."
    lines = []
    for d in docs[:limit]:
        authors = ", ".join(d.get("author_name", []) or ["unknown author"])
        year = d.get("first_publish_year", "?")
        isbn = (d.get("isbn") or [None])[0]
        key = d.get("key", "")
        lines.append(f"- {d.get('title', 'Untitled')} — {authors} ({year})"
                     f"{f', ISBN {isbn}' if isbn else ''} [{key}]")
    return "\n".join(lines)


async def _get(client: httpx.AsyncClient, olid_or_isbn: str) -> str:
    cleaned = olid_or_isbn.replace("-", "")
    if olid_or_isbn.startswith("/"):
        path = olid_or_isbn
    elif cleaned.isdigit() and len(cleaned) in (10, 13) or (
            len(cleaned) == 10 and cleaned[:9].isdigit() and cleaned[9] in "Xx"):
        path = f"/isbn/{cleaned}"
    else:
        path = f"/works/{olid_or_isbn}"
    try:
        resp = await client.get(f"{path}.json")
        resp.raise_for_status()
        d = resp.json()
    except (httpx.HTTPError, ValueError):
        return "Couldn't look up that book (Open Library request failed)."
    desc = d.get("description")
    if isinstance(desc, dict):
        desc = desc.get("value", "")
    return (f"{d.get('title', 'Untitled')} "
            f"({d.get('first_publish_date', '?')})\n{desc or 'No description.'}")


@mcp.tool()
async def search_books(query: str, limit: int = 5) -> str:
    """Search Open Library for books by title/author/keyword. Returns real titles,
    authors, publish years, and ISBNs — use this instead of guessing book facts."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
        return await _search(client, query, limit)


@mcp.tool()
async def get_book(olid_or_isbn: str) -> str:
    """Fetch details for one book by Open Library work key (e.g. /works/OL...W) or ISBN."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
        return await _get(client, olid_or_isbn)


if __name__ == "__main__":
    mcp.run()
