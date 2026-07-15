"""Background distillation: merge new raw-log observations into one section of
the capped memory blob on the fast model, behind a mechanical validation gate
(the book_recs.validate_recs convention — never trust the model's shape). Never
runs inline with an interactive request. Corrections are weighted above routine
queries; staleness decay is applied by code before the merge, not by the model."""

import re
from datetime import date

from lumen.daemon.llm import memory as memory_mod

MAX_ENTRIES_PER_SECTION = 40
DETAIL_CAP = 200

SYSTEM = (
    "You maintain a tiny, durable memory of ONE aspect of a user, for a private "
    "on-device assistant. You are given the CURRENT observations (markdown "
    "bullets) and NEW evidence from recent interactions. Return the UPDATED "
    "section: merge genuinely new, durable patterns into the existing bullets; "
    "reinforce an existing bullet by updating its date; drop nothing that is "
    "still plausibly true unless it is contradicted. CORRECTIONS (the user "
    "undoing, dismissing, or saying 'no, I meant…') are strong signals — weight "
    "them above routine queries. Keep it SHORT and specific: a handful of "
    "bullets, real patterns only, no filler or guesses. Every bullet ends with "
    "'(last seen YYYY-MM-DD)'. Reply with ONLY the section, starting with the "
    "exact header line '## {header}' followed by '- ' bullets. Today is {today}."
)


def summarize_entries(entries: list[dict]) -> str:
    """Compact, correction-first evidence block for the model."""
    lines = []
    for e in sorted(entries, key=lambda x: x.get("kind") != "correction"):
        tag = "CORRECTION" if e.get("kind") == "correction" else "query"
        detail = e.get("detail") or {}
        msg = str(detail.get("message") or detail.get("action") or detail)[:DETAIL_CAP]
        lines.append(f"[{tag}] {msg}")
    return "\n".join(lines[:MAX_ENTRIES_PER_SECTION])


def decay_bullets(bullets: list[str], now: date, max_age_days: int) -> list[str]:
    """Drop bullets whose last-seen date is older than max_age_days; keep any
    bullet with no parseable date (a hand-written note without a stamp)."""
    kept = []
    for b in bullets:
        m = memory_mod.BULLET_DATE.search(b)
        if m is None:
            kept.append(b)
            continue
        try:
            seen = date.fromisoformat(m.group(1))
        except ValueError:
            kept.append(b)
            continue
        if (now - seen).days <= max_age_days:
            kept.append(b)
    return kept


def _bullet_text(bullet: str) -> str:
    """A bullet's text with its leading '- ' and trailing date stripped."""
    t = memory_mod.BULLET_DATE.sub("", bullet).strip()
    return re.sub(r"^-\s*", "", t).strip()


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


def restamp_bullets(new_bullets: list[str], current_bullets: list[str],
                    today: date) -> list[str]:
    """Code owns the last-seen date — the 4B model can't be trusted to stamp it
    (observed '0000-00-00' live). Reuse a current bullet's date when the text is
    unchanged (so decay still fires on genuinely stale items); stamp today for
    anything new or reworded."""
    today_s = today.isoformat()
    prior = {}
    for c in current_bullets:
        m = memory_mod.BULLET_DATE.search(c)
        if m:
            prior[_norm(_bullet_text(c))] = m.group(1)
    out = []
    for b in new_bullets:
        text = _bullet_text(b)
        if not text:
            continue
        out.append(f"- {text} (last seen {prior.get(_norm(text), today_s)})")
    return out


def validate_section(text: str, header: str, cap: int) -> str | None:
    """Mechanical gate. Returns the section body (bullets joined by newlines,
    header stripped) if the model produced the right header with ≥1 dated
    bullet and it fits the cap; else None (caller keeps the previous content)."""
    parsed = memory_mod.parse(text)
    bullets = parsed.get(header) or []
    if not bullets:
        return None
    if not any(memory_mod.BULLET_DATE.search(b) for b in bullets):
        return None
    body = "\n".join(bullets)
    if len(f"## {header}\n{body}\n") > cap:
        return None
    return body


async def merge_section(llm, header: str, current_bullets: list[str],
                        entries: list[dict], now: date, cap: int
                        ) -> list[str] | None:
    """One merge pass for one section. Returns the new bullet list, or None on
    any failure — the caller then leaves that section untouched."""
    system = SYSTEM.format(header=header, today=now.isoformat())
    current = "\n".join(current_bullets) or "(none yet)"
    user = (f"CURRENT ## {header} observations:\n{current}\n\n"
            f"NEW evidence:\n{summarize_entries(entries)}")
    text = ""
    try:
        async for chunk in llm.chat([{"role": "system", "content": system},
                                     {"role": "user", "content": user}]):
            text += chunk
    except Exception:
        return None
    raw = memory_mod.parse(text).get(header) or []
    bullets = restamp_bullets(raw, current_bullets, now)
    if not bullets:
        return None
    if len(f"## {header}\n" + "\n".join(bullets) + "\n") > cap:
        return None
    return bullets
