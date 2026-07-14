"""Inbox triage digest: one tiny per-message verdict (the 4B loses track of
a 20-message batch — live 2026-07-13), digest rendered from the mirror rows
themselves. The model only picks a bucket and a reason; it can never misname
a sender or subject (mechanical grounding, book_recs rule-class)."""

import json
import re

SYSTEM = (
    "You classify ONE email from the user's inbox into exactly one bucket:\n"
    "- needs_response: a real person personally wrote to the user and is "
    "waiting on an answer from them.\n"
    "- worth_reading: informative mail worth the user's attention; no reply "
    "expected.\n"
    "- noise: newsletters, promotions, surveys, automated notifications, "
    "mass mail.\n"
    "Automated senders (no-reply, notify@, marketing blasts, feedback "
    "surveys) are never needs_response. Reply with ONLY a JSON object: "
    '{"bucket": "needs_response" | "worth_reading" | "noise", '
    '"why": "short reason"}.'
)

UNREAD_LIMIT = 15
INBOX_LIMIT = 15
MAX_MESSAGES = 20
SNIPPET_CAP = 120
WHY_CAP = 120

BUCKETS = ("needs_response", "worth_reading", "noise")

_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def message_line(r: dict) -> str:
    state = "read" if r.get("is_read") else "unread"
    day = (r.get("received_at") or "")[:10]
    snippet = (r.get("snippet") or "").strip()[:SNIPPET_CAP]
    return (f"[{state}] {day}: {r.get('sender', '')} — "
            f"“{r.get('subject') or '(no subject)'}” — {snippet}")


def parse_verdict(text: str) -> tuple[str, str] | None:
    """(bucket, why) from the model's JSON, or None — an unparseable verdict
    leaves the message honestly uncategorized, it never guesses a bucket."""
    m = _OBJECT.search(text or "")
    if m is None:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or obj.get("bucket") not in BUCKETS:
        return None
    return obj["bucket"], str(obj.get("why") or "").strip()[:WHY_CAP]


async def classify(llm, row: dict) -> tuple[str, str] | None:
    """One verdict pass for one message. LLMUnavailable propagates."""
    text = ""
    async for chunk in llm.chat([{"role": "system", "content": SYSTEM},
                                 {"role": "user", "content": message_line(row)}]):
        text += chunk
    return parse_verdict(text)


def render_digest(buckets: dict, total: int) -> str:
    """Deterministic rendering — every line's sender/subject/date comes from
    the mirror row, only the bucket and 'why' come from the model."""
    titles = (("needs_response", "NEEDS A RESPONSE"),
              ("worth_reading", "WORTH READING"),
              ("noise", "NOISE / NEWSLETTERS"))
    lines = [f"Inbox triage ({total} recent messages):"]
    used = 0
    for key, title in titles:
        lines.append(f"\n{title}:")
        items = buckets.get(key) or []
        if not items:
            lines.append("• nothing.")
        for row, why in items:
            used += 1
            day = (row.get("received_at") or "")[:10]
            reason = f": {why}" if why else ""
            lines.append(f"• {row.get('sender', '')} — "
                         f"“{row.get('subject') or '(no subject)'}” ({day}){reason}")
    left = total - used
    if left:
        lines.append(f"\n(didn't categorize {left} "
                     f"message{'s' if left != 1 else ''})")
    return "\n".join(lines)
