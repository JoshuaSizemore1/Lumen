"""Light per-announcement classifier: does this course announcement describe a
task or deadline the student should act on? One cheap pass on the resident small
model, run only on NEW (unclassified) announcements and bounded per sync — this
is the single model touch the Canvas feature is allowed (spec). Never invents an
obligation: it only flags and suggests text/date the announcement itself implies;
the user still confirms before any todo is created."""

import json
import re
from datetime import date

FLAG_CAP = 8
BODY_CAP = 2500
TEXT_CAP = 200

SYSTEM = (
    "You read one course announcement and decide if it asks the student to DO "
    "something with a deadline — an exam date, a reading/assignment, an RSVP, a "
    "form to submit. Reply with ONLY a JSON object: "
    '{"actionable": true|false, "text": a short imperative todo (or ""), '
    '"due": the date as YYYY-MM-DD if one is stated, else ""}. General news '
    "(office-hours moved, a welcome, a recap) is NOT actionable -> "
    '{"actionable": false, "text": "", "due": ""}.'
)

_OBJ = re.compile(r"\{.*\}", re.DOTALL)


def parse_flag(text: str) -> dict | None:
    m = _OBJ.search(text or "")
    if m is None:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def validate_flag(obj: dict) -> dict:
    actionable = bool(obj.get("actionable"))
    if not actionable:
        return {"actionable": False, "text": "", "due": None}
    text = str(obj.get("text") or "").strip()[:TEXT_CAP]
    if not text:
        return {"actionable": False, "text": "", "due": None}
    due = str(obj.get("due") or "").strip() or None
    if due is not None:
        try:
            date.fromisoformat(due)
        except ValueError:
            due = None
    return {"actionable": True, "text": text, "due": due}


async def classify(llm, announcement: dict) -> dict:
    user = (f"Title: {announcement.get('title') or '(none)'}\n\n"
            f"{(announcement.get('message') or '')[:BODY_CAP]}")
    reply = ""
    async for chunk in llm.chat([{"role": "system", "content": SYSTEM},
                                 {"role": "user", "content": user}]):
        reply += chunk
    obj = parse_flag(reply)
    return validate_flag(obj) if obj is not None else {
        "actionable": False, "text": "", "due": None}


async def flag_announcements(store, llm, *, cap: int = FLAG_CAP) -> dict:
    flagged = actionable = 0
    for ann in store.unclassified_announcements(cap):
        v = await classify(llm, ann)
        suggested = (json.dumps({"text": v["text"], "due": v["due"] or ""})
                     if v["actionable"] else None)
        store.set_announcement_flag(ann["id"], 1 if v["actionable"] else 0,
                                    suggested)
        flagged += 1
        actionable += 1 if v["actionable"] else 0
    return {"flagged": flagged, "actionable": actionable}
