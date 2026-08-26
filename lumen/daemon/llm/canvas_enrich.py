"""The 'Lumen powered' half of Canvas → Calendar: three bounded model passes that
add detail the API doesn't carry.

Built on canvas_flag.py's shape — capped, JSON-only, pure parse_/validate_ guards
around every model reply, and never allowed to fail the sync. All three are gated
on the AI switch.

Two hard rules:

* **Nothing inferred is ever auto-created.** Pass 1 only annotates an assignment
  that Canvas already told us about, so its output is safe to render. Passes 2
  and 3 invent an event out of prose, so they only ever write a *proposal* the
  user approves item by item.
* **The power budget is the design constraint, not an afterthought.** This runs
  on an iGPU laptop, on a model also serving chat. 8 (existing announcement flag)
  + 6 + 6 + 4 = 24 short calls per poll tick, with a wall-clock bailout on
  top, and `ai_state` is sticky so pass 1 drains to zero once a term is
  classified.
"""

import json
import logging
import re
import time
from datetime import date, datetime, timedelta

from lumen.daemon.connectors import canvas_events as ce

CLASSIFY_CAP = 6
EXAM_CAP = 6
STUDY_CAP = 4
BODY_CAP = 2500
SUMMARY_CAP = 240
BUDGET_SECONDS = 90.0        # whole-pass bailout; the mirror matters more

STUDY_HORIZON_DAYS = 21
STUDY_MINUTES = 90
STUDY_MAX_MINUTES = 180

TYPES = ("exam", "quiz", "project", "paper", "reading", "lab", "discussion",
         "other")

log = logging.getLogger("lumen.daemon")

_OBJ = re.compile(r"\{.*\}", re.DOTALL)

CLASSIFY_SYSTEM = (
    "You summarise ONE course assignment for a calendar entry. Reply with ONLY a "
    'JSON object: {"summary": one sentence, at most 30 words, saying what the '
    'student actually has to do, "type": one of '
    + "|".join(TYPES) + "}. Do not invent requirements that are not in the text. "
    'If the text says nothing useful, use {"summary": "", "type": "other"}.'
)

EXAM_SYSTEM = (
    "You read one course announcement and decide whether it states the date of an "
    "EXAM, midterm, final, or quiz. Reply with ONLY a JSON object: "
    '{"exam": true|false, "title": a short name for it, '
    '"date": YYYY-MM-DD, "start": HH:MM 24-hour or "", "end": HH:MM or ""}. '
    'Only true when an actual date is stated. A reminder about an exam whose '
    'date is not given is {"exam": false}.'
)


def _parse_obj(text: str) -> dict | None:
    match = _OBJ.search(text or "")
    if match is None:
        return None
    try:
        obj = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


# --- pass 1: classify --------------------------------------------------------
def validate_classification(obj: dict) -> dict:
    summary = str((obj or {}).get("summary") or "").strip()[:SUMMARY_CAP]
    kind = str((obj or {}).get("type") or "").strip().lower()
    if kind not in TYPES:
        kind = "other"
    return {"summary": summary, "type": kind}


async def classify_one(llm, row: dict) -> dict:
    body = ce.strip_html(row.get("description"), BODY_CAP)
    user = (f"Course: {row.get('course_code') or row.get('course_name') or '?'}\n"
            f"Title: {row.get('name') or '(untitled)'}\n"
            f"Submission types: {row.get('submission_types') or 'unknown'}\n\n"
            f"{body or '(no description)'}")
    reply = ""
    async for chunk in llm.chat([{"role": "system", "content": CLASSIFY_SYSTEM},
                                 {"role": "user", "content": user}]):
        reply += chunk
    obj = _parse_obj(reply)
    return validate_classification(obj) if obj is not None else {
        "summary": "", "type": "other"}


async def classify_assignments(store, llm, *, cap: int = CLASSIFY_CAP,
                               budget: float = BUDGET_SECONDS) -> dict:
    """Annotate unclassified assignments. `ai_state` is sticky either way, so a
    row is never paid for twice — that is what makes the budget drain to zero."""
    started = time.monotonic()
    done = failed = 0
    for row in store.unclassified_for_ai(cap):
        if time.monotonic() - started > budget:
            log.info("canvas classify: budget spent, %d done", done)
            break
        try:
            got = await classify_one(llm, row)
        except Exception:
            log.exception("canvas classify failed for %s", row.get("id"))
            store.set_ai_fields(row["id"], None, None, state=2)
            failed += 1
            continue
        store.set_ai_fields(row["id"], got["summary"] or None, got["type"], state=1)
        done += 1
    return {"classified": done, "failed": failed}


# --- pass 2: exam dates out of announcement prose ----------------------------
def validate_exam(obj: dict, *, today: date | None = None) -> dict | None:
    """None unless the reply names a real, future, parseable date. A model
    hallucinating '2026-02-30' or last month must not reach a proposal."""
    obj = obj or {}
    if not obj.get("exam"):
        return None
    title = str(obj.get("title") or "").strip()[:120]
    if not title:
        return None
    try:
        day = date.fromisoformat(str(obj.get("date") or "").strip())
    except (ValueError, TypeError):
        return None
    if today is not None and day < today:
        return None
    start = _clock(obj.get("start"))
    end = _clock(obj.get("end"))
    if start and end and end <= start:
        end = None
    return {"title": title, "date": day.isoformat(), "start": start, "end": end}


def _clock(value) -> str | None:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{1,2}:\d{2}", text):
        return None
    hour, minute = (int(p) for p in text.split(":"))
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    return f"{hour:02d}:{minute:02d}"


def exam_span(found: dict) -> tuple[str, str | None]:
    """(start, end) as the proposal stores them: a bare date when no time was
    stated, otherwise local ISO with a default one-hour length."""
    if not found["start"]:
        return found["date"], None
    start = f"{found['date']}T{found['start']}:00"
    if found["end"]:
        return start, f"{found['date']}T{found['end']}:00"
    later = datetime.fromisoformat(start) + timedelta(hours=1)
    return start, later.isoformat()


async def find_exam_dates(store, llm, proposals, *, cap: int = EXAM_CAP,
                          now: datetime | None = None,
                          budget: float = BUDGET_SECONDS) -> dict:
    """Mine announcements for exam dates. NEVER creates — writes a proposal."""
    now = now or datetime.now()
    started = time.monotonic()
    courses = store.courses_by_id()
    found = 0
    for ann in store.unclassified_announcements(cap):
        if time.monotonic() - started > budget:
            break
        try:
            reply = ""
            user = (f"Title: {ann.get('title') or '(none)'}\n\n"
                    f"{ce.strip_html(ann.get('message'), BODY_CAP)}")
            async for chunk in llm.chat(
                    [{"role": "system", "content": EXAM_SYSTEM},
                     {"role": "user", "content": user}]):
                reply += chunk
            got = validate_exam(_parse_obj(reply), today=now.date())
        except Exception:
            log.exception("canvas exam scan failed for %s", ann.get("id"))
            continue
        if got is None:
            continue
        cid = ann.get("course_id")
        # Don't propose an exam Canvas already has as an assignment, and don't
        # propose two for the same course on the same day.
        if _covered(store, cid, got["date"]) or proposals.has_exam_on(cid, got["date"]):
            continue
        start, end = exam_span(got)
        label = (courses.get(cid) or {}).get("course_code") or "Canvas"
        if proposals.add("exam", f"{label} — {got['title']}", start,
                         course_id=cid, end_at=end,
                         detail=f"From an announcement in {label}.",
                         source_kind="announcement", source_id=ann["id"]):
            found += 1
    return {"proposed": found}


def _covered(store, course_id, day: str) -> bool:
    for row in store.assignments(course_id):
        due = ce.due_local(row.get("due_at"))
        if due is not None and due.date().isoformat() == day:
            return True
    return False


# --- pass 3: study blocks (no model call at all) -----------------------------
def propose_study_blocks(store, proposals, events, *, cap: int = STUDY_CAP,
                         now: datetime | None = None) -> dict:
    """Place prep time before exams and projects in the user's own free slots.

    Deliberately deterministic — free_slots already knows how to find a gap, and
    asking a model to pick a time it cannot verify against the calendar would be
    strictly worse. The AI switch still gates it, because the `ai_type` it keys
    off only exists in AI mode."""
    from lumen.daemon.connectors.free_slots import find_slots
    now = (now or datetime.now()).astimezone()
    horizon = now + timedelta(days=STUDY_HORIZON_DAYS)
    made = 0
    for row in store.calendar_candidates():
        if made >= cap:
            break
        if (row.get("ai_type") or "").lower() not in ("exam", "project"):
            continue
        if not ce.should_keep(row):
            continue
        due = ce.due_local(row.get("due_at"))
        if due is None or not (now < due <= horizon):
            continue
        if proposals.was_dismissed("study", "assignment", row["id"], None):
            continue        # refused before, at any time — don't re-cost a slot
        window_end = (due - timedelta(days=1)).date()
        if window_end < now.date():
            continue
        busy = events.list_range(now.date().isoformat(), window_end.isoformat())
        slots = find_slots(busy, duration_min=STUDY_MINUTES,
                           start_date=now.date(), end_date=window_end,
                           now=now, count=1)
        if not slots:
            continue
        start, end = slots[0]
        if not _sane(start, end, now):
            continue
        label = row.get("course_code") or row.get("course_name") or "Canvas"
        if proposals.add("study", f"Prep — {label}: {row['name']}",
                         start.isoformat(), course_id=row.get("course_id"),
                         end_at=end.isoformat(),
                         detail=f"Suggested prep time before {row['name']} is due.",
                         source_kind="assignment", source_id=row["id"]):
            made += 1
    return {"proposed": made}


def _sane(start: datetime, end: datetime, now: datetime) -> bool:
    """A proposal must be forward-looking and of a believable length."""
    return (start > now and end > start
            and (end - start) <= timedelta(minutes=STUDY_MAX_MINUTES))
