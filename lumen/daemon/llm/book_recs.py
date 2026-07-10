"""Book recommendation pipeline: prompt -> tool loop (Open Library) -> lenient parse
-> mechanical grounding validation. A suggestion survives only if its title appears
in a tool result returned during this same request and isn't already in the catalog."""

import logging
import re
import time

log = logging.getLogger(__name__)

MAX_RECS = 3

_BULLET = re.compile(r"^\s*(?:[-*•]|\d{1,2}[.)])?\s*")  # one bullet/number token, not digits of a title


def parse_recs(text: str) -> list[dict]:
    """Parse 'Title | Author | rationale' lines; bullets/numbering tolerated.
    Two fields -> unknown author. Lines without a pipe are prose — skipped."""
    recs = []
    for line in (text or "").splitlines():
        parts = [p.strip() for p in _BULLET.sub("", line).split("|")]
        parts = [p for p in parts if p]
        if len(parts) >= 3:
            recs.append({"title": parts[0], "author": parts[1],
                         "rationale": " ".join(parts[2:])})
        elif len(parts) == 2:
            recs.append({"title": parts[0], "author": None, "rationale": parts[1]})
    return recs


def validate_recs(recs: list[dict], tool_results: list[str],
                  catalog: list[dict]) -> list[dict]:
    """Grounding gate: title must appear (case-insensitively) in this request's
    tool output; already-logged titles are dropped; deduped; capped at MAX_RECS."""
    haystack = "\n".join(tool_results).casefold()
    logged = {b["title"].strip().casefold() for b in catalog}
    out: list[dict] = []
    for r in recs:
        key = r["title"].strip().casefold()
        if not key or key in logged or key not in haystack:
            continue
        if any(o["title"].strip().casefold() == key for o in out):
            continue
        out.append(r)
    return out[:MAX_RECS]


REC_TOOL_NAMES = frozenset({"search_books", "get_book"})

SYSTEM_PROMPT = (
    "You help the user pick their next book. Use ONLY the search_books/get_book "
    "tools to find candidates — never answer from memory. Search for themes, "
    "authors, or genres the log shows the user enjoys (weight highly rated books). "
    "Never suggest a book already in their log.\n\n"
    "After searching, respond ONLY with up to 3 lines, one per suggestion, exactly:\n"
    "Title | Author | one short reason tied to specific books in the user's log\n"
    "Copy Title and Author exactly as they appear in the tool results."
)

DEFAULT_REQUEST = "Suggest up to 3 books I should read next."


def _rec_tools(bridge) -> list[dict]:
    """Only the books server's tools — collision-namespaced names still match."""
    tools = []
    for t in bridge.ollama_tools():
        name = t.get("function", {}).get("name", "")
        if name in REC_TOOL_NAMES or name.split("__")[-1] in REC_TOOL_NAMES:
            tools.append(t)
    return tools


async def recommend(llm, bridge, books, *, model=None, tool_log=None,
                    request=None, max_iterations=4) -> dict:
    """One pipeline for both entry points (screen button and chat). Returns
    {'recs', 'generated_at'} or {'error': honest message}; raises LLMUnavailable."""
    catalog = books.list_all()
    if not catalog:
        return {"error": "log a few books first — recommendations are grounded "
                         "in your catalog"}
    try:
        await bridge.ensure_started()
    except Exception:
        log.exception("MCP bridge unavailable for recommendations")
        return {"error": "book lookup tools are unavailable right now"}
    tools = _rec_tools(bridge)
    if not tools:
        return {"error": "book lookup tools are unavailable right now"}

    collected: list[str] = []

    async def executor(name, args):
        start = time.monotonic()
        try:
            text = await bridge.call(name, args)
            ok = True
            collected.append(text)
        except Exception as e:
            text, ok = f"tool error: {e}", False
        if tool_log is not None:
            tool_log.write(name, args, ok, text,
                           int((time.monotonic() - start) * 1000))
        return text

    messages = [
        {"role": "system", "content": f"{books.catalog_context()}\n\n{SYSTEM_PROMPT}"},
        {"role": "user", "content": request or DEFAULT_REQUEST},
    ]
    final = ""
    async for ev in llm.chat_with_tools(messages, tools, executor,
                                        model=model, max_iterations=max_iterations):
        if "content" in ev:
            final = ev["content"]
    recs = validate_recs(parse_recs(final), collected, catalog)
    if not recs:
        return {"error": "couldn't get grounded suggestions right now — try again"}
    books.save_recs(recs)
    return books.latest_recs()
