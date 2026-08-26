"""The 'Lumen powered' passes. Every model reply is untrusted input; these tests
are mostly about what must NOT get through."""

import time
from datetime import date, datetime, timedelta

import pytest

from lumen.daemon import db
from lumen.daemon.connectors.canvas_proposals import CanvasProposals
from lumen.daemon.connectors.canvas_store import CanvasStore
from lumen.daemon.connectors.gcal import EventStore
from lumen.daemon.llm import canvas_enrich as en


class FakeLLM:
    """Replays canned strings, one per call."""

    def __init__(self, *replies):
        self._replies = list(replies)
        self.calls = 0

    async def chat(self, messages, **kw):
        self.calls += 1
        reply = self._replies.pop(0) if self._replies else "{}"
        if isinstance(reply, Exception):
            raise reply
        yield reply


@pytest.fixture
def store(tmp_path):
    conn = db.connect(tmp_path / "e.db")
    st = CanvasStore(conn)
    st.upsert_courses([{"id": 1, "name": "Software Practice II",
                        "course_code": "CS 3505"}])
    return st


def seed(store, **kw):
    row = {"id": 10, "course_id": 1, "name": "HW1",
           "due_at": (datetime.now().astimezone() + timedelta(days=7)).isoformat(),
           "points": 100.0, "html_url": "u",
           "description": "<p>Write a <b>sprite</b> loader.</p>",
           "submitted": False}
    store.upsert_assignments([{**row, **kw}], "stamp")


# --- pass 1: classify --------------------------------------------------------
@pytest.mark.parametrize("raw, expect", [
    ('{"summary": "Write a loader.", "type": "project"}',
     {"summary": "Write a loader.", "type": "project"}),
    ('{"summary": "x", "type": "NONSENSE"}', {"summary": "x", "type": "other"}),
    ('{"type": "exam"}', {"summary": "", "type": "exam"}),
    ('{}', {"summary": "", "type": "other"}),
])
def test_validate_classification(raw, expect):
    import json
    assert en.validate_classification(json.loads(raw)) == expect


def test_classification_summary_is_capped():
    got = en.validate_classification({"summary": "x" * 5000, "type": "exam"})
    assert len(got["summary"]) == en.SUMMARY_CAP


async def test_classify_writes_the_fields_and_marks_done(store):
    seed(store)
    llm = FakeLLM('{"summary": "Write a sprite loader.", "type": "project"}')
    out = await en.classify_assignments(store, llm)
    assert out == {"classified": 1, "failed": 0}
    row = store.calendar_candidates()[0]
    assert row["ai_summary"] == "Write a sprite loader."
    assert row["ai_type"] == "project" and row["ai_state"] == 1


async def test_a_classified_row_is_never_paid_for_twice(store):
    """ai_state is sticky, which is what drains the budget to zero over a term."""
    seed(store)
    llm = FakeLLM('{"summary": "s", "type": "reading"}')
    await en.classify_assignments(store, llm)
    await en.classify_assignments(store, llm)
    assert llm.calls == 1


async def test_a_failing_call_is_marked_and_not_retried_forever(store):
    seed(store)
    llm = FakeLLM(RuntimeError("model is down"))
    out = await en.classify_assignments(store, llm)
    assert out == {"classified": 0, "failed": 1}
    assert store.calendar_candidates()[0]["ai_state"] == 2


async def test_garbage_from_the_model_degrades_to_other(store):
    seed(store)
    await en.classify_assignments(store, FakeLLM("I'm afraid I can't do that."))
    row = store.calendar_candidates()[0]
    assert row["ai_type"] == "other" and row["ai_state"] == 1


async def test_classify_respects_the_cap(store):
    for i in range(10):
        seed(store, id=100 + i, name=f"HW{i}")
    llm = FakeLLM(*['{"summary": "s", "type": "reading"}'] * 10)
    await en.classify_assignments(store, llm, cap=3)
    assert llm.calls == 3


async def test_classify_bails_out_on_the_wall_clock(store):
    """The power budget is a hard constraint, not an afterthought."""
    for i in range(6):
        seed(store, id=200 + i, name=f"HW{i}")

    class Slow(FakeLLM):
        async def chat(self, messages, **kw):
            self.calls += 1
            time.sleep(0.02)
            yield '{"summary": "s", "type": "reading"}'

    llm = Slow()
    await en.classify_assignments(store, llm, cap=6, budget=0.03)
    assert llm.calls < 6


async def test_submitted_and_archived_rows_are_never_classified(store):
    seed(store, id=11, name="Done", submitted=True)
    seed(store, id=12, name="NoDue", due_at=None)
    llm = FakeLLM(*['{"summary": "s", "type": "reading"}'] * 5)
    await en.classify_assignments(store, llm)
    assert llm.calls == 0


# --- pass 2: exam dates ------------------------------------------------------
TODAY = date(2026, 8, 25)


@pytest.mark.parametrize("obj", [
    {"exam": False},
    {"exam": True, "title": "", "date": "2026-09-10"},
    {"exam": True, "title": "Midterm", "date": "not a date"},
    {"exam": True, "title": "Midterm", "date": "2026-02-30"},   # impossible
    {"exam": True, "title": "Midterm", "date": "2026-01-01"},   # in the past
    {"exam": True, "title": "Midterm"},
    {},
])
def test_validate_exam_rejects_junk(obj):
    assert en.validate_exam(obj, today=TODAY) is None


def test_validate_exam_accepts_a_real_date():
    got = en.validate_exam({"exam": True, "title": "Midterm 1",
                            "date": "2026-09-10", "start": "10:00",
                            "end": "11:30"}, today=TODAY)
    assert got == {"title": "Midterm 1", "date": "2026-09-10",
                   "start": "10:00", "end": "11:30"}


@pytest.mark.parametrize("bad", ["25:00", "10:99", "1000", "", None, "ten"])
def test_a_nonsense_clock_becomes_no_time(bad):
    got = en.validate_exam({"exam": True, "title": "M", "date": "2026-09-10",
                            "start": bad}, today=TODAY)
    assert got["start"] is None


def test_an_end_before_its_start_is_dropped():
    got = en.validate_exam({"exam": True, "title": "M", "date": "2026-09-10",
                            "start": "14:00", "end": "09:00"}, today=TODAY)
    assert got["end"] is None


def test_exam_span_defaults_to_one_hour():
    assert en.exam_span({"date": "2026-09-10", "start": "10:00", "end": None}) == (
        "2026-09-10T10:00:00", "2026-09-10T11:00:00")


def test_exam_span_with_no_time_is_a_bare_date():
    assert en.exam_span({"date": "2026-09-10", "start": None, "end": None}) == (
        "2026-09-10", None)


async def test_find_exam_dates_writes_a_proposal_and_never_an_event(tmp_path, store):
    conn = db.connect(tmp_path / "p.db")
    proposals = CanvasProposals(conn)
    store.upsert_announcements([{"id": 5, "course_id": 1, "title": "Midterm",
                                 "posted_at": "2026-08-20T00:00:00Z",
                                 "message": "Midterm on Sept 10 at 10am",
                                 "html_url": "a"}])
    llm = FakeLLM('{"exam": true, "title": "Midterm 1", "date": "2026-09-10", '
                  '"start": "10:00", "end": "11:30"}')
    out = await en.find_exam_dates(store, llm, proposals,
                                   now=datetime(2026, 8, 25))
    assert out == {"proposed": 1}
    item = proposals.pending()[0]
    assert item["title"] == "CS 3505 — Midterm 1"
    assert item["start_at"] == "2026-09-10T10:00:00"
    assert item["status"] == "pending"          # NOT on the calendar


async def test_an_exam_canvas_already_knows_about_is_not_proposed(tmp_path, store):
    """No point proposing a date that is already a due-dated assignment."""
    conn = db.connect(tmp_path / "p.db")
    proposals = CanvasProposals(conn)
    seed(store, id=20, name="Midterm",
         due_at=datetime(2026, 9, 10, 16, 0).astimezone().isoformat())
    store.upsert_announcements([{"id": 5, "course_id": 1, "title": "Midterm",
                                 "posted_at": "2026-08-20T00:00:00Z",
                                 "message": "m", "html_url": "a"}])
    llm = FakeLLM('{"exam": true, "title": "Midterm", "date": "2026-09-10"}')
    out = await en.find_exam_dates(store, llm, proposals,
                                   now=datetime(2026, 8, 25))
    assert out == {"proposed": 0}


async def test_exam_scan_survives_a_raising_model(tmp_path, store):
    conn = db.connect(tmp_path / "p.db")
    store.upsert_announcements([{"id": 5, "course_id": 1, "title": "T",
                                 "posted_at": "2026-08-20T00:00:00Z",
                                 "message": "m", "html_url": "a"}])
    out = await en.find_exam_dates(store, FakeLLM(RuntimeError("down")),
                                   CanvasProposals(conn),
                                   now=datetime(2026, 8, 25))
    assert out == {"proposed": 0}


# --- pass 3: study blocks ----------------------------------------------------
def test_study_blocks_are_placed_in_a_free_slot(tmp_path, store):
    conn = db.connect(tmp_path / "s.db")
    proposals, events = CanvasProposals(conn), EventStore(conn)
    seed(store, id=30, name="Final Project")
    store.set_ai_fields(30, "Build it.", "project")
    out = en.propose_study_blocks(store, proposals, events)
    assert out == {"proposed": 1}
    item = proposals.pending()[0]
    assert item["kind"] == "study" and item["title"].startswith("Prep — CS 3505")
    start = datetime.fromisoformat(item["start_at"])
    end = datetime.fromisoformat(item["end_at"])
    assert start > datetime.now().astimezone()
    assert end - start == timedelta(minutes=en.STUDY_MINUTES)


def test_only_exams_and_projects_get_prep_time(tmp_path, store):
    conn = db.connect(tmp_path / "s.db")
    proposals, events = CanvasProposals(conn), EventStore(conn)
    seed(store, id=31, name="Reading")
    store.set_ai_fields(31, "Read it.", "reading")
    assert en.propose_study_blocks(store, proposals, events) == {"proposed": 0}


def test_an_unclassified_assignment_gets_no_prep_time(tmp_path, store):
    """ai_type only exists in AI mode, which is what gates this pass."""
    conn = db.connect(tmp_path / "s.db")
    seed(store, id=32)
    assert en.propose_study_blocks(store, CanvasProposals(conn),
                                   EventStore(conn)) == {"proposed": 0}


def test_prep_time_is_not_proposed_beyond_the_horizon(tmp_path, store):
    conn = db.connect(tmp_path / "s.db")
    far = (datetime.now().astimezone()
           + timedelta(days=en.STUDY_HORIZON_DAYS + 10)).isoformat()
    seed(store, id=33, name="Far Exam", due_at=far)
    store.set_ai_fields(33, "s", "exam")
    assert en.propose_study_blocks(store, CanvasProposals(conn),
                                   EventStore(conn)) == {"proposed": 0}


def test_a_dismissed_prep_block_does_not_come_back(tmp_path, store):
    conn = db.connect(tmp_path / "s.db")
    proposals, events = CanvasProposals(conn), EventStore(conn)
    seed(store, id=34, name="Project")
    store.set_ai_fields(34, "s", "project")
    en.propose_study_blocks(store, proposals, events)
    proposals.dismiss(proposals.pending()[0]["id"])
    en.propose_study_blocks(store, proposals, events)
    assert proposals.count() == 0


def test_study_blocks_respect_the_cap(tmp_path, store):
    conn = db.connect(tmp_path / "s.db")
    proposals, events = CanvasProposals(conn), EventStore(conn)
    for i in range(8):
        seed(store, id=40 + i, name=f"Project {i}")
        store.set_ai_fields(40 + i, "s", "project")
    assert en.propose_study_blocks(store, proposals, events,
                                   cap=2) == {"proposed": 2}


def test_a_dismissed_prep_block_stays_gone_even_if_the_free_slot_moves(tmp_path, store):
    """The bug this guards: a study block's time comes from whatever slot is
    free, so it moves as the calendar fills. Keying the refusal on the exact
    time meant dismissing an 08:00 block simply brought it back at 11:00."""
    conn = db.connect(tmp_path / "s.db")
    proposals, events = CanvasProposals(conn), EventStore(conn)
    seed(store, id=35, name="Project")
    store.set_ai_fields(35, "s", "project")
    en.propose_study_blocks(store, proposals, events)
    first = proposals.pending()[0]
    proposals.dismiss(first["id"])

    # The user books something over that slot, so the next free one is elsewhere.
    start = datetime.fromisoformat(first["start_at"])
    events.replace_window([{
        "id": "e1", "calendar_id": "primary", "calendar_name": "P", "color": None,
        "title": "Booked", "start_at": start.isoformat(),
        "end_at": (start + timedelta(hours=3)).isoformat(), "all_day": 0,
        "location": None, "description": None, "attendees": [],
        "status": "confirmed"}],
        start.date().isoformat(), start.date().isoformat())

    en.propose_study_blocks(store, proposals, events)
    assert proposals.count() == 0


def test_a_dismissed_exam_still_keys_on_its_date(tmp_path):
    """The other half: a different date really is a different claim, so refusing
    Sept 10 must not suppress a later correction to Sept 12."""
    conn = db.connect(tmp_path / "x.db")
    proposals = CanvasProposals(conn)
    pid = proposals.add("exam", "Midterm", "2026-09-10T10:00:00",
                        source_kind="announcement", source_id=5)
    proposals.dismiss(pid)
    assert proposals.add("exam", "Midterm", "2026-09-10T10:00:00",
                         source_kind="announcement", source_id=5) is None
    assert proposals.add("exam", "Midterm", "2026-09-12T10:00:00",
                         source_kind="announcement", source_id=5) is not None
