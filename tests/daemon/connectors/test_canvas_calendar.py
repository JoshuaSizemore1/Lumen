"""The Canvas → Calendar orchestrator: plan / execute / commit."""

import inspect
import os
import time
from datetime import datetime

import pytest

from lumen.daemon import db
from lumen.daemon.connectors import canvas_calendar as cc
from lumen.daemon.connectors import canvas_events as ce
from lumen.daemon.connectors.canvas_alerts import CanvasAlerts
from lumen.daemon.connectors.canvas_prefs import CanvasPrefs
from lumen.daemon.connectors.canvas_queue import CanvasQueue
from lumen.daemon.connectors.canvas_store import CanvasStore


@pytest.fixture(autouse=True)
def mountain_time():
    prev = os.environ.get("TZ")
    os.environ["TZ"] = "America/Denver"
    time.tzset()
    yield
    if prev is None:
        del os.environ["TZ"]
    else:
        os.environ["TZ"] = prev
    time.tzset()


NOW = datetime(2026, 8, 25, 9, 0)


class FakeWriter:
    def __init__(self, *, create="evt_new", patch=True, delete=True):
        self.created, self.patched, self.deleted = [], [], []
        self._create, self._patch, self._delete = create, patch, delete

    def create_event(self, body):
        self.created.append(body)
        return self._create

    def patch_event(self, event_id, body):
        self.patched.append((event_id, body))
        return self._patch

    def delete_event(self, event_id):
        self.deleted.append(event_id)
        return self._delete


@pytest.fixture
def bits(tmp_path):
    conn = db.connect(tmp_path / "c.db")
    store = CanvasStore(conn)
    prefs = CanvasPrefs(conn)
    prefs.set_sync_enabled(True)
    store.upsert_courses([{"id": 1, "name": "Software Practice II",
                           "course_code": "CS 3505"}])
    return store, prefs, CanvasQueue(conn), CanvasAlerts(conn)


def seed(store, **kw):
    row = {"id": 10, "course_id": 1, "name": "HW1",
           "due_at": "2026-09-02T05:59:59Z", "points": 100.0,
           "html_url": "https://c/a/1", "description": None, "submitted": False}
    store.upsert_assignments([{**row, **kw}], "stamp-1")


# --- plan --------------------------------------------------------------------
def test_plan_is_empty_while_the_switch_is_off(bits):
    store, prefs, _q, _a = bits
    prefs.set_sync_enabled(False)
    seed(store)
    assert cc.plan_calendar(store, prefs, NOW) == []


def test_plan_creates_when_the_switch_is_on(bits):
    store, prefs, _q, _a = bits
    seed(store)
    actions = cc.plan_calendar(store, prefs, NOW)
    assert [a.op for a in actions] == ["create"]
    assert actions[0].title == "CS 3505 — HW1 due"


def test_plan_is_not_gated_on_a_linked_todo(bits):
    """The old marker path only pushed assignments that had a todo."""
    store, prefs, _q, _a = bits
    seed(store)
    assert store.calendar_candidates()[0]["todo_id"] is None
    assert cc.plan_calendar(store, prefs, NOW)


def test_candidates_include_the_rows_that_are_removal_cases(bits):
    """active_assignments() filters these out, which is exactly why the diff
    engine cannot be fed from it."""
    store, prefs, _q, _a = bits
    seed(store)
    store.set_assignment_dismissed(10, True)
    assert store.active_assignments() == []
    assert len(store.calendar_candidates()) == 1


# --- execute -----------------------------------------------------------------
def test_execute_takes_no_store(bits):
    """Structural proof that SQLite cannot be touched from the worker thread —
    the daemon's single-writer rule depends on it."""
    params = set(inspect.signature(cc.execute_calendar).parameters)
    assert params == {"writer", "actions"}


def test_execute_creates_silently(bits):
    store, prefs, _q, _a = bits
    seed(store)
    writer = FakeWriter()
    results = cc.execute_calendar(writer, cc.plan_calendar(store, prefs, NOW))
    assert len(writer.created) == 1
    body = writer.created[0]
    assert body["summary"] == "CS 3505 — HW1 due"
    assert body["start"]["dateTime"] == "2026-09-01T23:44:59-06:00"
    assert body["extendedProperties"]["private"]["assignment_id"] == "10"
    assert results[0].ok and results[0].event_id == "evt_new"


def test_execute_never_deletes_for_a_removal(bits):
    """The whole safety property: the background loop only proposes."""
    store, prefs, _q, _a = bits
    seed(store, submitted=True)
    store.set_calendar_event(10, "evt_1", "timed", "s", "sig")
    actions = cc.plan_calendar(store, prefs, NOW)
    assert [a.op for a in actions] == ["remove"]
    writer = FakeWriter()
    cc.execute_calendar(writer, actions)
    assert writer.deleted == []


def test_execute_survives_a_missing_writer(bits):
    store, prefs, _q, _a = bits
    seed(store)
    results = cc.execute_calendar(None, cc.plan_calendar(store, prefs, NOW))
    assert results[0].ok is False


def test_execute_survives_a_raising_writer(bits):
    class Boom(FakeWriter):
        def create_event(self, body):
            raise RuntimeError("network")

    store, prefs, _q, _a = bits
    seed(store)
    results = cc.execute_calendar(Boom(), cc.plan_calendar(store, prefs, NOW))
    assert results[0].ok is False


def test_recreate_deletes_then_inserts(bits):
    store, prefs, _q, _a = bits
    seed(store)
    store.set_calendar_event(10, "evt_old", "all_day", "2026-09-01", "sig")
    actions = cc.plan_calendar(store, prefs, NOW)
    assert [a.op for a in actions] == ["recreate"]
    writer = FakeWriter()
    cc.execute_calendar(writer, actions)
    assert writer.deleted == ["evt_old"] and len(writer.created) == 1


# --- commit ------------------------------------------------------------------
def _cycle(store, prefs, queue, alerts, writer, now=NOW):
    actions = cc.plan_calendar(store, prefs, now)
    results = cc.execute_calendar(writer, actions)
    return cc.commit_calendar(store, queue, alerts, results)


def test_a_full_cycle_links_the_event(bits):
    store, prefs, queue, alerts = bits
    seed(store)
    counts = _cycle(store, prefs, queue, alerts, FakeWriter())
    assert counts["created"] == 1
    row = store.calendar_candidates()[0]
    assert row["calendar_event_id"] == "evt_new"
    assert row["event_kind"] == "timed" and row["event_sig"]


def test_a_second_cycle_is_a_no_op(bits):
    """Idempotence: nothing changed, so nothing is written."""
    store, prefs, queue, alerts = bits
    seed(store)
    writer = FakeWriter()
    _cycle(store, prefs, queue, alerts, writer)
    assert _cycle(store, prefs, queue, alerts, writer) == {
        "created": 0, "updated": 0, "recreated": 0, "queued": 0,
        "failed": 0, "demoted": 0}
    assert len(writer.created) == 1


def test_a_moved_due_date_updates_silently_and_leaves_an_alert(bits):
    store, prefs, queue, alerts = bits
    seed(store)
    writer = FakeWriter()
    _cycle(store, prefs, queue, alerts, writer)
    seed(store, due_at="2026-09-04T05:59:59Z")          # the professor moved it
    counts = _cycle(store, prefs, queue, alerts, writer)
    assert counts["updated"] == 1
    assert writer.patched[0][0] == "evt_new"            # same event, kept notes
    assert queue.count() == 0                           # NO confirmation
    assert alerts.count() == 1 and "moved" in alerts.recent()[0]["detail"]


def test_submitting_queues_a_removal_rather_than_deleting(bits):
    store, prefs, queue, alerts = bits
    seed(store)
    writer = FakeWriter()
    _cycle(store, prefs, queue, alerts, writer)
    seed(store, submitted=True)
    counts = _cycle(store, prefs, queue, alerts, writer)
    assert counts["queued"] == 1
    assert writer.deleted == []
    assert queue.pending()[0]["reason"] == "submitted"


def test_a_removal_is_not_requeued_every_sync(bits):
    """The background poll re-proposes the same removal on every tick; the partial
    unique index keeps exactly one pending row."""
    store, prefs, queue, alerts = bits
    seed(store)
    writer = FakeWriter()
    _cycle(store, prefs, queue, alerts, writer)
    seed(store, submitted=True)
    for _ in range(4):
        _cycle(store, prefs, queue, alerts, writer)
    assert queue.count() == 1


def test_a_hand_deleted_event_demotes_to_create(bits):
    """A patch that 404s must clear the link, or the state machine wedges on an
    event id that can never be patched again."""
    store, prefs, queue, alerts = bits
    seed(store)
    _cycle(store, prefs, queue, alerts, FakeWriter())
    seed(store, name="HW1 (revised)")
    counts = _cycle(store, prefs, queue, alerts, FakeWriter(patch=False))
    assert counts["demoted"] == 1
    assert store.calendar_candidates()[0]["calendar_event_id"] is None
    # ...and the next pass simply creates it again.
    assert _cycle(store, prefs, queue, alerts, FakeWriter())["created"] == 1


def test_a_failed_create_leaves_no_link(bits):
    store, prefs, queue, alerts = bits
    seed(store)
    counts = _cycle(store, prefs, queue, alerts, FakeWriter(create=None))
    assert counts["failed"] == 1
    assert store.calendar_candidates()[0]["calendar_event_id"] is None


def test_conflicts_become_alerts(bits):
    store, prefs, queue, alerts = bits
    seed(store)
    actions = cc.plan_calendar(store, prefs, NOW)
    busy = [{"start_at": "2026-09-01T23:30:00-06:00",
             "end_at": "2026-09-02T00:30:00-06:00", "all_day": 0,
             "title": "Study group"}]
    results = cc.execute_calendar(FakeWriter(), actions)
    cc.commit_calendar(store, queue, alerts, results,
                       ce.conflicts(actions, busy))
    details = [a["detail"] for a in alerts.recent()]
    assert any("Study group" in d for d in details)


# --- resolving a queued removal ----------------------------------------------
def _queued(bits):
    store, prefs, queue, alerts = bits
    seed(store)
    writer = FakeWriter()
    _cycle(store, prefs, queue, alerts, writer)
    seed(store, submitted=True)
    _cycle(store, prefs, queue, alerts, writer)
    return store, queue, queue.pending()[0]["id"]


def test_approving_a_removal_deletes_the_event(bits):
    store, queue, qid = _queued(bits)
    writer = FakeWriter()
    assert cc.apply_removal(writer, store, queue, qid, True) == {
        "ok": True, "removed": True}
    assert writer.deleted == ["evt_new"]
    assert queue.count() == 0
    assert store.calendar_candidates()[0]["calendar_event_id"] is None


def test_declining_a_removal_clears_the_link(bits):
    """Otherwise the diff engine still sees an event on an ineligible assignment
    and re-queues the identical removal on every single sync, forever."""
    store, queue, qid = _queued(bits)
    writer = FakeWriter()
    assert cc.apply_removal(writer, store, queue, qid, False)["removed"] is False
    assert writer.deleted == []
    assert queue.count() == 0
    assert store.calendar_candidates()[0]["calendar_event_id"] is None


def test_a_declined_removal_does_not_come_back(bits):
    store, prefs, queue, alerts = bits
    seed(store)
    writer = FakeWriter()
    _cycle(store, prefs, queue, alerts, writer)
    seed(store, submitted=True)
    _cycle(store, prefs, queue, alerts, writer)
    cc.apply_removal(writer, store, queue, queue.pending()[0]["id"], False)
    for _ in range(3):
        _cycle(store, prefs, queue, alerts, writer)
    assert queue.count() == 0


def test_resolving_a_stale_id_is_refused(bits):
    store, queue, qid = _queued(bits)
    cc.apply_removal(FakeWriter(), store, queue, qid, True)
    assert cc.apply_removal(FakeWriter(), store, queue, qid, True)["ok"] is False


def test_a_failed_delete_keeps_the_row_pending(bits):
    store, queue, qid = _queued(bits)
    assert cc.apply_removal(FakeWriter(delete=False), store, queue,
                            qid, True)["ok"] is False
    assert queue.count() == 1          # still there to retry


def test_ai_mode_changes_the_body(bits):
    store, prefs, queue, alerts = bits
    seed(store)
    store.set_ai_fields(10, "Build the sprite loader.", "project")
    prefs.set_ai_mode(True)
    writer = FakeWriter()
    _cycle(store, prefs, queue, alerts, writer)
    assert writer.created[0]["description"].startswith("Build the sprite loader.")
    assert writer.created[0]["colorId"] == ce.COLOR_PROJECT


def test_proposal_body_all_day_end_is_exclusive():
    # A date-only exam proposal stores end_at NULL (canvas_enrich.exam_span).
    # Google's all-day end is EXCLUSIVE: end == start is a zero-length event
    # and a 400, which left the proposal stuck pending forever.
    body = cc.proposal_body({"title": "CS 250 — Midterm", "kind": "exam",
                             "start_at": "2026-09-14", "end_at": None,
                             "detail": ""})
    assert body["start"] == {"date": "2026-09-14"}
    assert body["end"] == {"date": "2026-09-15"}
    assert body["colorId"] == str(ce.COLOR_EXAM)


def test_proposal_body_keeps_a_timed_span_untouched():
    body = cc.proposal_body({"title": "CS 250 — Final", "kind": "exam",
                             "start_at": "2026-09-14T10:00:00",
                             "end_at": "2026-09-14T11:00:00", "detail": "Rm 4"})
    assert body["start"]["dateTime"] == "2026-09-14T10:00:00"
    assert body["end"]["dateTime"] == "2026-09-14T11:00:00"
