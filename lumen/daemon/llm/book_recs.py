"""Book recommendation pipeline: prompt -> tool loop (Open Library) -> lenient parse
-> mechanical grounding validation. A suggestion survives only if its title appears
in a tool result returned during this same request and isn't already in the catalog."""

import re

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
