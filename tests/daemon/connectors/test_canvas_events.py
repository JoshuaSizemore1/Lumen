"""The Canvas → Calendar diff engine. Pure module, so this is where the
behaviour is pinned down exhaustively — the orchestrator around it only pipes."""

import os
import time
from datetime import datetime, timedelta

import pytest

from lumen.daemon.connectors import canvas_events as ce


@pytest.fixture(autouse=True)
def mountain_time():
    """Pin the local zone: every span assertion here is local-time arithmetic."""
    prev = os.environ.get("TZ")
    os.environ["TZ"] = "America/Denver"
    time.tzset()
    yield
    if prev is None:
        del os.environ["TZ"]
    else:
        os.environ["TZ"] = prev
    time.tzset()


NOW = datetime(2026, 8, 25, 9, 0).astimezone()


def row(**kw):
    base = {"id": 10, "course_id": 1, "name": "HW1",
            "due_at": "2026-09-02T05:59:59Z",     # 23:59:59 local on Sep 1
            "points": 100.0, "html_url": "https://utah.instructure.com/a/1",
            "submitted": 0, "dismissed": 0, "handled": 0, "missing_syncs": 0,
            "course_active": 1, "course_included": 1, "course_code": "CS 3505",
            "course_name": "Software Practice II", "calendar_event_id": None,
            "event_kind": None, "event_start": None, "event_sig": None,
            "is_quiz": 0, "submission_types": "online_upload",
            "ai_summary": None, "ai_type": None}
    return {**base, **kw}


def linked(**kw):
    """A row whose event already exists and is exactly in sync."""
    r = row(**kw)
    want = ce.render(r, kw.get("_ai", False))
    r.update(calendar_event_id="evt_1", event_kind=want["kind"],
             event_start=want["start"], event_sig=want["sig"])
    return r


# --- time --------------------------------------------------------------------
def test_due_local_converts_utc_to_local():
    assert ce.due_local("2026-09-02T05:59:59Z").isoformat() == "2026-09-01T23:59:59-06:00"


@pytest.mark.parametrize("bad", [None, "", "not a date", "2026-13-45", 17])
def test_due_local_is_total(bad):
    assert ce.due_local(bad) is None


def test_due_local_rejects_a_naive_timestamp():
    """A naive string has no instant; guessing a zone would silently shift the
    event by up to a day."""
    assert ce.due_local("2026-09-02T05:59:59") is None


def test_timed_span_starts_fifteen_minutes_before_the_due_time():
    start, end, kind = ce.event_span(ce.due_local("2026-09-02T05:59:59Z"))
    assert kind == "timed"
    assert start == "2026-09-01T23:44:59-06:00"
    assert end == "2026-09-01T23:59:59-06:00"


def test_local_midnight_falls_back_to_all_day():
    start, end, kind = ce.event_span(ce.due_local("2026-09-02T06:00:00Z"))
    assert kind == "all_day"
    assert (start, end) == ("2026-09-02", "2026-09-03")   # end is exclusive


def test_span_is_correct_across_the_spring_forward_gap():
    """Doing the subtraction on the local wall clock can land on an hour that
    does not exist. In UTC it stays a plain 15 minutes, and the emitted string
    carries its offset, so the instant is unambiguous either way."""
    due = ce.due_local("2026-03-08T09:05:00Z")            # 03:05 MDT, just after
    start, end, _kind = ce.event_span(due)
    delta = datetime.fromisoformat(end) - datetime.fromisoformat(start)
    assert delta == timedelta(minutes=15)
    assert start == "2026-03-08T02:50:00-06:00"


def test_span_is_correct_across_the_fall_back_overlap():
    due = ce.due_local("2026-11-01T08:05:00Z")
    start, end, _kind = ce.event_span(due)
    assert datetime.fromisoformat(end) - datetime.fromisoformat(start) == timedelta(minutes=15)


# --- eligibility -------------------------------------------------------------
def test_a_plain_upcoming_assignment_is_creatable():
    assert ce.eligible_for_create(row(), NOW) is True


def test_beyond_the_horizon_is_not_created():
    far = (NOW + timedelta(days=ce.HORIZON_DAYS + 5)).astimezone().isoformat()
    assert ce.eligible_for_create(row(due_at=_utc(far)), NOW) is False


def test_a_past_due_date_is_not_created():
    assert ce.eligible_for_create(row(due_at="2026-08-01T05:59:59Z"), NOW) is False


def test_the_horizon_gates_creation_but_NOT_retention():
    """The trap: gate retention on the horizon too and an event that merely
    drifts past day 90 starts proposing its own deletion, nagging about work the
    user never asked to remove."""
    far = row(due_at=_utc((NOW + timedelta(days=200)).astimezone().isoformat()))
    assert ce.eligible_for_create(far, NOW) is False
    assert ce.should_keep(far) is True
    far_linked = {**far, "calendar_event_id": "evt_1"}
    assert [a.op for a in ce.plan([far_linked], NOW)] != ["remove"]


@pytest.mark.parametrize("field, value, reason", [
    ("submitted", 1, "submitted"),
    ("dismissed", 1, "dismissed"),
    ("course_active", 0, "archived"),
    ("course_included", 0, "archived"),
    ("due_at", None, "no_due"),
    ("missing_syncs", ce.GONE_STREAK, "gone"),
])
def test_removal_reasons(field, value, reason):
    assert ce.removal_reason(row(**{field: value})) == reason


def test_no_reason_means_keep():
    assert ce.removal_reason(row()) is None
    assert ce.should_keep(row()) is True


def test_reason_precedence_is_stable():
    """A submitted assignment in an archived course reads better as 'submitted';
    one that also vanished reads as 'gone'."""
    assert ce.removal_reason(row(submitted=1, course_included=0)) == "submitted"
    assert ce.removal_reason(
        row(submitted=1, course_included=0, missing_syncs=9)) == "gone"
    assert ce.removal_reason(row(dismissed=1, course_included=0)) == "dismissed"


def test_one_missing_sync_is_not_yet_gone():
    """A single flaky fetch must never look like a deleted term."""
    assert ce.removal_reason(row(missing_syncs=1)) is None
    assert ce.removal_reason(row(missing_syncs=ce.GONE_STREAK)) == "gone"


def test_handled_does_NOT_remove_a_calendar_event():
    """`handled` means the user deleted the linked TODO. Honouring it here would
    make deleting a todo silently delete a calendar event."""
    assert ce.removal_reason(row(handled=1)) is None
    assert [a.op for a in ce.plan([linked(handled=1)], NOW)] == []


def test_a_todo_link_is_not_required():
    """The old marker path only ever pushed assignments that happened to have a
    linked todo. Calendar sync is not gated on that."""
    assert ce.eligible_for_create(row(todo_id=None), NOW) is True


# --- rendering ---------------------------------------------------------------
def test_title_and_description_are_deterministic():
    r = row()
    assert ce.title(r) == "CS 3505 — HW1 due"
    desc = ce.description(r)
    assert "CS 3505 — Software Practice II" in desc
    assert "Due Tue, Sep 1 at 11:59 PM" in desc
    assert "100 points" in desc
    assert r["html_url"] in desc
    assert desc.endswith(ce.FOOTER)


def test_description_omits_absent_metadata():
    desc = ce.description(row(points=None, html_url=None))
    assert "points" not in desc and "http" not in desc


def test_basic_type_comes_from_canvas_not_a_model():
    assert ce.basic_type(row(is_quiz=1)) == "quiz"
    assert ce.basic_type(row(submission_types="online_quiz")) == "quiz"
    assert ce.basic_type(row(submission_types="discussion_topic")) == "discussion"
    assert ce.basic_type(row(submission_types="online_upload")) == "project"
    assert ce.basic_type(row(submission_types="online_text_entry")) is None


def test_ai_mode_prepends_the_summary_and_names_the_type():
    r = row(ai_summary="Implement the sprite sheet loader.", ai_type="project")
    desc = ce.description(r, ai=True)
    assert desc.startswith("Implement the sprite sheet loader.")
    assert "Project" in desc
    assert ce.description(r, ai=False).startswith("CS 3505")


def test_ai_colors_differ_by_type():
    assert ce.color_id(row(ai_type="exam"), ai=True) == ce.COLOR_EXAM
    assert ce.color_id(row(ai_type="reading"), ai=True) == ce.COLOR_READING
    assert ce.color_id(row(is_quiz=1)) == ce.COLOR_QUIZ
    assert ce.color_id(row(submission_types="online_text_entry")) == ce.COLOR_DEFAULT


def test_an_exam_title_drops_the_word_due():
    assert ce.title(row(name="Midterm 1", ai_type="exam"), ai=True) == "CS 3505 — Midterm 1"


def test_signature_is_stable_and_content_sensitive():
    a = ce.render(row())
    assert ce.render(row())["sig"] == a["sig"]
    assert ce.render(row(points=50.0))["sig"] != a["sig"]
    assert ce.render(row(name="HW2"))["sig"] != a["sig"]


def test_strip_html_flattens_a_canvas_description():
    got = ce.strip_html("<p>Read <b>chapter&nbsp;3</b></p><ul><li>Part A</li></ul>")
    assert "chapter 3" in got and "• Part A" in got and "<" not in got


@pytest.mark.parametrize("bad", [None, "", 0])
def test_strip_html_is_total(bad):
    assert ce.strip_html(bad) == ""


# --- plan() ------------------------------------------------------------------
def test_plan_creates_an_unlinked_eligible_assignment():
    actions = ce.plan([row()], NOW)
    assert [a.op for a in actions] == ["create"]
    assert actions[0].event_id is None
    assert actions[0].kind == "timed"


def test_plan_is_quiet_when_everything_matches():
    assert ce.plan([linked()], NOW) == []


def test_plan_updates_on_a_content_change():
    r = linked()
    r["name"] = "HW1 (revised)"
    actions = ce.plan([r], NOW)
    assert [a.op for a in actions] == ["update"]
    assert actions[0].event_id == "evt_1"


def test_a_moved_due_date_patches_rather_than_recreating():
    """Keeping the Google event id keeps any notes the user added to the event.
    Only a change of SHAPE forces a delete+insert, because Google will not
    cleanly patch a timed event into an all-day one."""
    r = linked()
    r["due_at"] = "2026-09-03T05:59:59Z"
    actions = ce.plan([r], NOW)
    assert [a.op for a in actions] == ["update"]
    assert actions[0].event_id == "evt_1"
    assert ce.moved(actions[0]) is True


def test_a_shape_change_recreates():
    r = linked()
    r["due_at"] = "2026-09-03T06:00:00Z"          # now local midnight → all-day
    actions = ce.plan([r], NOW)
    assert [a.op for a in actions] == ["recreate"]
    assert actions[0].kind == "all_day"
    assert actions[0].event_id == "evt_1"


def test_moved_is_false_for_a_body_only_change():
    r = linked()
    r["points"] = 55.0
    assert ce.moved(ce.plan([r], NOW)[0]) is False


@pytest.mark.parametrize("field, value, reason", [
    ("submitted", 1, "submitted"),
    ("dismissed", 1, "dismissed"),
    ("course_included", 0, "archived"),
    ("missing_syncs", ce.GONE_STREAK, "gone"),
])
def test_plan_queues_a_removal_for_a_linked_ineligible_row(field, value, reason):
    actions = ce.plan([linked(**{field: value})], NOW)
    assert [a.op for a in actions] == ["remove"]
    assert actions[0].reason == reason
    assert actions[0].event_id == "evt_1"


def test_an_unlinked_ineligible_row_produces_nothing():
    """Nothing to remove and nothing to create — the common resting state."""
    assert ce.plan([row(submitted=1)], NOW) == []


def test_plan_ignores_an_unlinked_row_with_no_due_date():
    assert ce.plan([row(due_at=None)], NOW) == []


def test_ai_toggle_turns_matching_events_into_updates():
    """Flipping the switch re-renders the body, which changes the signature.
    Producing updates here is the intended behaviour, not churn."""
    r = linked()
    r["ai_summary"], r["ai_type"] = "Build the loader.", "project"
    assert ce.plan([r], NOW) == []                      # basic mode: unchanged
    assert [a.op for a in ce.plan([r], NOW, ai=True)] == ["update"]


def test_plan_handles_a_mixed_batch():
    renamed = linked(id=5)
    renamed["name"] = "Renamed"          # link first, THEN drift
    rows = [row(id=1), linked(id=2), linked(id=3, submitted=1),
            row(id=4, due_at=None), renamed]
    ops = {a.assignment_id: a.op for a in ce.plan(rows, NOW)}
    assert ops == {1: "create", 3: "remove", 5: "update"}


# --- conflicts ---------------------------------------------------------------
def test_conflicts_flags_an_overlapping_timed_event():
    action = ce.plan([row()], NOW)[0]
    busy = [{"start_at": "2026-09-01T23:30:00-06:00",
             "end_at": "2026-09-02T00:30:00-06:00", "all_day": 0,
             "title": "Study group"}]
    assert len(ce.conflicts([action], busy)) == 1


def test_conflicts_ignores_a_non_overlapping_event():
    action = ce.plan([row()], NOW)[0]
    free = [{"start_at": "2026-09-01T18:00:00-06:00",
             "end_at": "2026-09-01T19:00:00-06:00", "all_day": 0}]
    assert ce.conflicts([action], free) == []


def test_conflicts_ignores_all_day_events_on_both_sides():
    """An all-day event overlaps everything, which would make the flag useless."""
    action = ce.plan([row()], NOW)[0]
    assert ce.conflicts([action], [{"start_at": "2026-09-01", "all_day": 1}]) == []
    allday = ce.plan([row(due_at="2026-09-02T06:00:00Z")], NOW)[0]
    assert ce.conflicts([allday], [{"start_at": "2026-09-01T23:00:00-06:00",
                                    "end_at": "2026-09-02T01:00:00-06:00",
                                    "all_day": 0}]) == []


def test_conflicts_never_considers_removals():
    removal = ce.plan([linked(submitted=1)], NOW)[0]
    assert ce.conflicts([removal], [{"start_at": "2026-09-01T23:30:00-06:00",
                                     "end_at": "2026-09-02T00:30:00-06:00",
                                     "all_day": 0}]) == []


def test_conflicts_tolerate_unparseable_rows():
    action = ce.plan([row()], NOW)[0]
    assert ce.conflicts([action], [{"start_at": None}, {"start_at": "junk"},
                                   {}]) == []


def _utc(local_iso: str) -> str:
    """Local ISO -> the RFC3339 UTC string Canvas would have sent."""
    from datetime import timezone
    return (datetime.fromisoformat(local_iso).astimezone(timezone.utc)
            .isoformat().replace("+00:00", "Z"))
