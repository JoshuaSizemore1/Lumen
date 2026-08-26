"""Canvas → Calendar orchestration, deliberately split in three:

    plan_calendar(store, prefs, now)               loop thread, SQLite READ
    execute_calendar(writer, actions)              WORKER thread, NO SQLite
    commit_calendar(store, queue, alerts, results) loop thread, SQLite WRITE

The split is the point, not ceremony. Google's client is blocking, and a first
sync with forty due dates is forty round trips; running that on the event loop
would freeze the daemon — and therefore the whole UI — for tens of seconds.
`execute_calendar` takes no store precisely so it *cannot* touch SQLite from a
worker thread, which would break the daemon's single-writer rule.

The other rule this file exists to enforce:

    The background loop only ever PROPOSES. Every external mutation that needs
    the user's assent is executed inside a request the user just initiated.

Silent create / update / reschedule is the explicitly-decided exception — only a
disappearance is queued for review.
"""

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from lumen.daemon.connectors import canvas_events as ce
from lumen.daemon.connectors.gcal import canvas_event_body

log = logging.getLogger("lumen.daemon")


@dataclass
class Result:
    action: ce.Action
    ok: bool = False
    event_id: str | None = None
    demoted: bool = False        # patch hit a hand-deleted event; link cleared


def body_for(action: ce.Action) -> dict:
    return canvas_event_body(
        action.title, action.start, action.end, kind=action.kind,
        description=action.description, color_id=action.color_id,
        html_url=action.html_url, assignment_id=action.assignment_id)


# --- 1. plan (loop thread, reads) --------------------------------------------
def plan_calendar(store, prefs, now) -> list[ce.Action]:
    """What the calendar should become. Empty when the switch is off, so the
    feature costs exactly nothing until the user turns it on."""
    if not prefs.sync_enabled():
        return []
    return ce.plan(store.calendar_candidates(), now, ai=prefs.ai_mode())


# --- 2. execute (worker thread, no SQLite) -----------------------------------
def execute_calendar(writer, actions: list[ce.Action]) -> list[Result]:
    """Perform the silent half against Google. Removals are passed through
    untouched — they are only ever queued, never executed here."""
    results: list[Result] = []
    for action in actions:
        if action.op == "remove":
            results.append(Result(action=action, ok=True,
                                  event_id=action.event_id))
            continue
        if writer is None:
            results.append(Result(action=action, ok=False))
            continue
        try:
            results.append(_apply(writer, action))
        except Exception:
            log.exception("canvas calendar action failed: %s", action.op)
            results.append(Result(action=action, ok=False))
    return results


def _apply(writer, action: ce.Action) -> Result:
    if action.op == "create":
        eid = writer.create_event(body_for(action))
        return Result(action=action, ok=eid is not None, event_id=eid)
    if action.op == "update":
        if writer.patch_event(action.event_id, body_for(action)):
            return Result(action=action, ok=True, event_id=action.event_id)
        # The user deleted it by hand. Clearing the link demotes the next pass to
        # a plain create, instead of retrying a patch that can never succeed.
        return Result(action=action, ok=False, demoted=True)
    if action.op == "recreate":
        writer.delete_event(action.event_id)     # 404 already counts as success
        eid = writer.create_event(body_for(action))
        return Result(action=action, ok=eid is not None, event_id=eid)
    return Result(action=action, ok=False)


# --- 3. commit (loop thread, writes) -----------------------------------------
def commit_calendar(store, queue, alerts, results: list[Result],
                    conflicts: list | None = None) -> dict:
    counts = {"created": 0, "updated": 0, "recreated": 0, "queued": 0,
              "failed": 0, "demoted": 0}
    for res in results:
        action = res.action
        if action.op == "remove":
            if queue is not None and queue.add(
                    action.assignment_id, action.event_id, action.title,
                    action.reason, detail=action.start):
                counts["queued"] += 1
            continue
        if res.demoted:
            store.clear_calendar_event(action.assignment_id)
            counts["demoted"] += 1
            continue
        if not res.ok:
            counts["failed"] += 1
            continue
        store.set_calendar_event(action.assignment_id, res.event_id,
                                 action.kind, action.start, action.sig)
        counts[{"create": "created", "update": "updated",
                "recreate": "recreated"}[action.op]] += 1
        if alerts is not None and ce.moved(action):
            alerts.add(ce.DUE_MOVED_KIND,
                       f"{action.title}: moved to {action.start}",
                       assignment_id=action.assignment_id)
    if alerts is not None and conflicts:
        for action, event in conflicts:
            alerts.add(ce.CONFLICT_KIND,
                       f"{action.title} overlaps "
                       f"{event.get('title') or 'an existing event'}",
                       assignment_id=action.assignment_id)
    return counts


# --- resolving a queued removal (inside a user-initiated request) ------------
# Split like the sync path, for the same reason: the Google delete is blocking,
# and a user-initiated route still runs on the event loop.
def begin_removal(queue, qid: int) -> dict | None:
    """Loop thread. The pending row, or None if it isn't pending (double click,
    stale UI) — which is what stops a second click deleting twice."""
    return queue.get(qid)


def finish_removal(store, queue, row: dict, approve: bool,
                   deleted: bool) -> dict:
    """Loop thread, writes.

    The decline branch MUST clear the link too. Leaving it set means the diff
    engine still sees an event attached to an ineligible assignment and re-queues
    the identical removal on every sync, forever."""
    if approve and not deleted:
        return {"ok": False, "reason": "calendar unavailable"}
    queue.resolve(row["id"], "removed" if approve else "declined")
    if row["assignment_id"] is not None:
        store.clear_calendar_event(row["assignment_id"])
    return {"ok": True, "removed": bool(approve)}


async def resolve_removal(writer, store, queue, qid: int, approve: bool) -> dict:
    """The whole thing, with the blocking delete off the event loop."""
    import asyncio
    row = begin_removal(queue, qid)
    if row is None:
        return {"ok": False, "reason": "not pending"}
    deleted = False
    if approve:
        if writer is None:
            return {"ok": False, "reason": "calendar unavailable"}
        deleted = await asyncio.to_thread(writer.delete_event, row["event_id"])
    return finish_removal(store, queue, row, approve, deleted)


def apply_removal(writer, store, queue, qid: int, approve: bool) -> dict:
    """Synchronous convenience for callers where blocking is fine."""
    row = begin_removal(queue, qid)
    if row is None:
        return {"ok": False, "reason": "not pending"}
    deleted = False
    if approve:
        if writer is None:
            return {"ok": False, "reason": "calendar unavailable"}
        deleted = writer.delete_event(row["event_id"])
    return finish_removal(store, queue, row, approve, deleted)


def proposal_body(row: dict) -> dict:
    """The Google body for an accepted AI proposal. Pure, so the router's accept
    path has nothing to compute on the loop thread."""
    start = str(row["start_at"])
    timed = "T" in start
    color = ce.COLOR_EXAM if row["kind"] == "exam" else ce.COLOR_PROJECT
    if timed:
        end = row["end_at"] or start
    else:
        # A date-only exam stores end_at as NULL. Google's all-day end is
        # EXCLUSIVE, so end == start is a zero-length event and a 400 — the
        # day AFTER the last covered day is what it wants.
        end = str(row["end_at"] or start).split("T")[0]
        end = (date.fromisoformat(end) + timedelta(days=1)).isoformat()
    return canvas_event_body(
        row["title"], start, end,
        kind="timed" if timed else "all_day",
        description=row.get("detail") or "", color_id=color)
