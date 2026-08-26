"""Canvas → Calendar diff engine. Pure: no SQLite, no Qt, no LLM, no network.

Given the mirrored assignment rows and the clock, decide what the calendar
should look like and emit the actions to get there. Everything that could be
argued about lives here, where it can be tested exhaustively, so the
orchestrator around it stays a thin pipe.

Three rules that the obvious port from the old `pending_markers` gets wrong, and
that the tests pin down:

* **The 90-day horizon gates creation ONLY, never retention.** Gate retention on
  it too and an event that simply drifts past day 90 starts proposing its own
  deletion, nagging the user about work they never asked to remove.
* **`handled` is deliberately ignored.** It means "the user deleted the *todo*".
  Reusing it here would make deleting a todo silently delete a calendar event —
  two different objects, one flag.
* **A vanished assignment must be absent twice** (GONE_STREAK). One flaky fetch
  returning a short list would otherwise read as "the term was deleted" and queue
  a mass removal.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

HORIZON_DAYS = 90
LEAD_MINUTES = 15
GONE_STREAK = 2

# Removal reasons, most-specific first. Precedence matters: a submitted
# assignment in an archived course reads better as "submitted".
REMOVAL_PRECEDENCE = ("gone", "submitted", "dismissed", "archived", "no_due")

# Google's fixed palette (colorId 1-11).
COLOR_EXAM = "11"        # tomato
COLOR_QUIZ = "6"         # tangerine
COLOR_PROJECT = "5"      # banana
COLOR_READING = "7"      # peacock
COLOR_DEFAULT = "8"      # graphite
AI_COLORS = {"exam": COLOR_EXAM, "quiz": COLOR_QUIZ, "project": COLOR_PROJECT,
             "reading": COLOR_READING, "paper": COLOR_PROJECT,
             "lab": COLOR_PROJECT, "discussion": COLOR_READING}

DUE_MOVED_KIND = "due_moved"
CONFLICT_KIND = "conflict"

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
FOOTER = "Added by Lumen from Canvas."


@dataclass(frozen=True)
class Action:
    """One calendar mutation. `remove` is never executed by the sync loop — it is
    queued for the user (see canvas_calendar.py)."""
    op: str                      # create | update | recreate | remove
    assignment_id: int
    title: str = ""
    start: str = ""              # local ISO, or YYYY-MM-DD when all-day
    end: str = ""
    kind: str = "timed"          # timed | all_day
    sig: str = ""
    description: str = ""
    color_id: str | None = None
    html_url: str | None = None
    event_id: str | None = None
    reason: str | None = None    # removals only
    old_start: str | None = None


def strip_html(raw: str | None, cap: int = 2000) -> str:
    """HTML → readable text. A daemon-side copy on purpose: the UI screen has one
    too, but importing UI code into the daemon to save nine lines would couple
    the two halves of the app across the process boundary."""
    if not raw:
        return ""
    text = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", raw, flags=re.I)
    text = re.sub(r"<li[^>]*>", "• ", text, flags=re.I)
    text = _TAG.sub(" ", text)
    for entity, char in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                         ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        text = text.replace(entity, char)
    text = _WS.sub(" ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()[:cap]


# --- time ---------------------------------------------------------------------
def due_local(due_at: str | None) -> datetime | None:
    """Canvas's RFC3339 UTC instant as an aware LOCAL datetime, or None."""
    if not due_at:
        return None
    try:
        parsed = datetime.fromisoformat(str(due_at).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone()


def is_all_day(due_dt: datetime) -> bool:
    """Canvas assignment objects carry no all_day field, so local midnight is the
    only available signal. A professor who sets a literal 00:00 due time does get
    an all-day event; that is a known, accepted false positive."""
    return (due_dt.hour, due_dt.minute, due_dt.second) == (0, 0, 0)


def event_span(due_dt: datetime) -> tuple[str, str, str]:
    """(start, end, kind). A timed event ends AT the due time and starts
    LEAD_MINUTES before it, so the reminder lands while there is still time to
    act. The subtraction happens in UTC and is converted back, which keeps it
    correct across a DST boundary — doing it on the local wall clock can land on
    an hour that does not exist."""
    if is_all_day(due_dt):
        day = due_dt.date().isoformat()
        return day, (due_dt.date() + timedelta(days=1)).isoformat(), "all_day"
    end_utc = due_dt.astimezone(timezone.utc)
    start = (end_utc - timedelta(minutes=LEAD_MINUTES)).astimezone(due_dt.tzinfo)
    return start.isoformat(), due_dt.isoformat(), "timed"


# --- eligibility --------------------------------------------------------------
def removal_reason(row: dict) -> str | None:
    """Why this assignment's event should not exist, or None to keep it.

    NOTE the absence of `handled`: see the module docstring."""
    reasons = []
    if (row.get("missing_syncs") or 0) >= GONE_STREAK:
        reasons.append("gone")
    if row.get("submitted"):
        reasons.append("submitted")
    if row.get("dismissed"):
        reasons.append("dismissed")
    if not row.get("course_active", 1) or not row.get("course_included", 1):
        reasons.append("archived")
    if due_local(row.get("due_at")) is None:
        reasons.append("no_due")
    for candidate in REMOVAL_PRECEDENCE:
        if candidate in reasons:
            return candidate
    return None


def should_keep(row: dict) -> bool:
    """Retention. Deliberately has NO horizon term — see the module docstring."""
    return removal_reason(row) is None


def eligible_for_create(row: dict, now: datetime) -> bool:
    """Creation additionally requires the due date to be ahead of us and inside
    the horizon. An event in the past carries no reminder value, and beyond the
    horizon it is noise the user did not ask for yet."""
    if not should_keep(row):
        return False
    due = due_local(row.get("due_at"))
    if due is None:
        return False
    now = now.astimezone(due.tzinfo) if now.tzinfo else now.astimezone()
    return now <= due <= now + timedelta(days=HORIZON_DAYS)


# --- rendering ----------------------------------------------------------------
def course_label(row: dict) -> str:
    return (row.get("course_code") or row.get("course_name") or "Canvas").strip()


def title(row: dict, ai: bool = False) -> str:
    label = course_label(row)
    name = (row.get("name") or "Assignment").strip()
    kind = ai and (row.get("ai_type") or "")
    if kind and kind.lower() in ("exam", "quiz", "midterm", "final"):
        return f"{label} — {name}"
    return f"{label} — {name} due"


def _pretty_due(due_dt: datetime) -> str:
    if is_all_day(due_dt):
        return due_dt.strftime("%a, %b %-d")
    return due_dt.strftime("%a, %b %-d at %-I:%M %p")


def basic_type(row: dict) -> str | None:
    """A type hint with no model involved: Canvas already tells us."""
    if row.get("is_quiz"):
        return "quiz"
    types = (row.get("submission_types") or "").lower()
    if "quiz" in types:
        return "quiz"
    if "discussion" in types:
        return "discussion"
    if "upload" in types:
        return "project"
    return None


def color_id(row: dict, ai: bool = False) -> str:
    kind = (row.get("ai_type") or "").lower() if ai else None
    return AI_COLORS.get(kind or basic_type(row) or "", COLOR_DEFAULT)


def description(row: dict, ai: bool = False) -> str:
    """Deterministic string building in basic mode; AI mode prepends the model's
    summary and a Type: line. That changes the signature, so flipping the switch
    naturally re-renders existing events as `update` actions — which is correct."""
    due = due_local(row.get("due_at"))
    lines = []
    if ai:
        summary = (row.get("ai_summary") or "").strip()
        if summary:
            lines += [summary, ""]
    lines.append(f"{course_label(row)}"
                 + (f" — {row['course_name']}" if row.get("course_name")
                    and row.get("course_code") else ""))
    meta = []
    if due is not None:
        meta.append(f"Due {_pretty_due(due)}")
    points = row.get("points")
    if points:
        meta.append(f"{points:g} points")
    kind = (row.get("ai_type") if ai else None) or basic_type(row)
    if kind:
        meta.append(str(kind).title())
    if meta:
        lines.append(" · ".join(meta))
    if row.get("html_url"):
        lines.append(str(row["html_url"]))
    lines += ["", FOOTER]
    return "\n".join(lines)


def signature(title_: str, description_: str, color: str | None, kind: str,
              start: str) -> str:
    """Content fingerprint. Drives `update`: a changed body with an unchanged
    start would otherwise be invisible to the diff."""
    blob = "\x1f".join([title_, description_, color or "", kind, start])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def render(row: dict, ai: bool = False) -> dict | None:
    """The event this assignment *should* have, or None if it has no due date."""
    due = due_local(row.get("due_at"))
    if due is None:
        return None
    start, end, kind = event_span(due)
    t = title(row, ai)
    desc = description(row, ai)
    color = color_id(row, ai)
    return {"title": t, "description": desc, "color_id": color, "kind": kind,
            "start": start, "end": end, "html_url": row.get("html_url"),
            "sig": signature(t, desc, color, kind, start)}


# --- the diff -----------------------------------------------------------------
def plan(rows: list[dict], now: datetime, ai: bool = False) -> list[Action]:
    """The whole decision, as data.

    `update` covers a due-date move as well as a content change: patching keeps
    the Google event id, and with it any notes the user added to the event. Only
    a change of *shape* (timed ↔ all-day) forces a delete+insert, because Google
    will not cleanly patch one into the other."""
    actions: list[Action] = []
    for row in rows:
        aid = row.get("id")
        linked = row.get("calendar_event_id")
        reason = removal_reason(row)
        if reason is not None:
            if linked:
                actions.append(Action(
                    op="remove", assignment_id=aid, event_id=linked,
                    title=row.get("event_title") or title(row, ai),
                    reason=reason, start=row.get("event_start") or "",
                    kind=row.get("event_kind") or "timed"))
            continue
        want = render(row, ai)
        if want is None:
            continue
        base = dict(op="", assignment_id=aid, title=want["title"],
                    start=want["start"], end=want["end"], kind=want["kind"],
                    sig=want["sig"], description=want["description"],
                    color_id=want["color_id"], html_url=want["html_url"],
                    event_id=linked)
        if not linked:
            if eligible_for_create(row, now):
                actions.append(Action(**{**base, "op": "create"}))
            continue
        if (row.get("event_kind") or "timed") != want["kind"]:
            actions.append(Action(**{**base, "op": "recreate",
                                     "old_start": row.get("event_start")}))
        elif (row.get("event_start") != want["start"]
                or row.get("event_sig") != want["sig"]):
            actions.append(Action(**{**base, "op": "update",
                                     "old_start": row.get("event_start")}))
    return actions


def moved(action: Action) -> bool:
    """A due date actually shifted (as opposed to only the body changing) — what
    a `due_moved` alert is raised from."""
    return bool(action.old_start and action.old_start != action.start)


# --- conflicts (deterministic, never a model call) ----------------------------
def _parse(value: str):
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def conflicts(actions: list[Action], existing: list[dict]) -> list[tuple]:
    """(action, event) pairs whose times overlap an event already on the
    calendar. Timed events only — an all-day marker overlaps everything, which
    would make the flag useless."""
    out = []
    for action in actions:
        if action.op == "remove" or action.kind != "timed":
            continue
        a_start, a_end = _parse(action.start), _parse(action.end)
        if a_start is None or a_end is None:
            continue
        for event in existing:
            if event.get("all_day"):
                continue
            e_start = _parse(event.get("start_at"))
            e_end = _parse(event.get("end_at")) or e_start
            if e_start is None:
                continue
            if e_start.tzinfo is None or a_start.tzinfo is None:
                e_start = e_start.replace(tzinfo=a_start.tzinfo)
                e_end = e_end.replace(tzinfo=a_start.tzinfo)
            if a_start < e_end and e_start < a_end:
                out.append((action, event))
    return out
