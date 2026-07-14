"""Commitment tracking: scan SENT mail for promises the user made and store
them as suggestions pending confirmation. Grounding is mechanical — an
extracted item survives only if its quote appears verbatim (normalized) in
the email body, so the model can't invent obligations."""

import json
import re
from datetime import date, datetime, timedelta

SYSTEM = (
    "You scan an email the user SENT for commitments the user made to someone "
    "else — promises like \"I'll send that over Friday\" or \"I will call you "
    "Monday\". Reply with ONLY a JSON array. Each item: "
    '{"text": short imperative todo phrasing of the commitment, '
    '"due": the promised date as YYYY-MM-DD resolved from the send date, or "" '
    'if none was named, "quote": the exact sentence from the email containing '
    "the promise, copied verbatim}. Routine sign-offs (\"talk soon\", \"let me "
    "know\") are not commitments. No commitments -> []."
)

BODY_CAP = 4000
TEXT_CAP = 200
FIRST_RUN_DAYS = 90
MAX_EMAILS_PER_SCAN = 20

_ARRAY = re.compile(r"\[.*\]", re.DOTALL)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


def parse_commitments(text: str) -> list | None:
    m = _ARRAY.search(text or "")
    if m is None:
        return None
    try:
        items = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return items if isinstance(items, list) else None


def validate(items: list, body: str) -> list[dict]:
    """Mechanical gate: quote must be found in the body; text non-empty;
    an unparseable due date degrades to None rather than dropping the item."""
    body_norm = _norm(body or "")
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        text = str(it.get("text") or "").strip()[:TEXT_CAP]
        quote = str(it.get("quote") or "").strip()
        if not text or not quote or _norm(quote) not in body_norm:
            continue
        due = str(it.get("due") or "").strip() or None
        if due is not None:
            try:
                date.fromisoformat(due)
            except ValueError:
                due = None
        out.append({"text": text, "due_date": due, "quote": quote})
    return out


async def scan(llm, mail_store, suggestions, *, now: datetime | None = None,
               max_emails: int = MAX_EMAILS_PER_SCAN) -> dict:
    """Walk SENT mail forward from the cursor (bounded), one extraction pass
    per message. LLMUnavailable propagates before the cursor advances, so a
    failed scan simply retries the same mail next time."""
    now = now or datetime.now().astimezone()
    cursor = suggestions.last_scan() or (
        now - timedelta(days=FIRST_RUN_DAYS)).isoformat()
    emails = mail_store.sent(cursor, limit=max_emails)
    scanned = found = 0
    for m in emails:
        scanned += 1
        if suggestions.has_email(m["id"]):
            continue
        sent_day = (m.get("received_at") or "")[:10]
        user = (f"Sent on {sent_day}. Subject: {m.get('subject') or '(none)'}\n\n"
                f"{(m.get('body') or '')[:BODY_CAP]}")
        text = ""
        async for chunk in llm.chat([{"role": "system", "content": SYSTEM},
                                     {"role": "user", "content": user}]):
            text += chunk
        for it in validate(parse_commitments(text) or [], m.get("body") or ""):
            suggestions.add(it["text"], it["due_date"], it["quote"],
                            m["id"], m.get("subject"))
            found += 1
    if emails:
        suggestions.set_last_scan(emails[-1]["received_at"])
    return {"scanned": scanned, "found": found}
