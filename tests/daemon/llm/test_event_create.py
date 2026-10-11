"""Event proposal extraction + the mechanical validation gate. The gate, not the
model, is what guarantees nothing malformed ever reaches a confirm dialog."""

from datetime import datetime, timedelta, timezone

import pytest

from lumen.daemon.llm.event_create import (
    confirm_payload, parse_proposal, propose_event, validate_proposal,
)

TZ = timezone(timedelta(hours=-6))
NOW = datetime(2026, 7, 10, 14, 0, tzinfo=TZ)


@pytest.fixture(autouse=True)
def mountain_time(set_tz):
    set_tz("America/Denver")        # TZ is Denver's summer offset


def proposal(**kw):
    base = {"title": "Call with Sam", "start": "2026-07-11T14:00",
            "end": "2026-07-11T14:30"}
    base.update(kw)
    return base


def valid(p, message="book a call with Sam tomorrow at 2pm"):
    return validate_proposal(p, now=NOW, user_message=message)


# ---- parsing ----

def test_parse_tolerates_prose_and_fences():
    text = 'Sure! Here is the event:\n```json\n{"title": "X", "start": "2026-07-11T14:00"}\n```'
    assert parse_proposal(text) == {"title": "X", "start": "2026-07-11T14:00"}


def test_parse_no_json_returns_none():
    assert parse_proposal("I could not find a time.") is None
    assert parse_proposal("") is None


# ---- validation gate ----

def test_valid_proposal_normalizes_with_local_tz():
    p, err = valid(proposal())
    assert err is None
    assert p["title"] == "Call with Sam"
    assert p["start"] == "2026-07-11T14:00:00-06:00"   # naive times become local
    assert p["end"] == "2026-07-11T14:30:00-06:00"
    assert p["all_day"] is False and p["attendees"] == []


def test_time_after_dst_ends_gets_that_days_offset():
    """todo-fixes #73: "Dentist Nov 5 2pm", asked in October, was stamped
    with October's −06:00 — booked at 1 PM, though the confirm said 2 PM."""
    p, err = valid(proposal(start="2026-11-05T14:00", end="2026-11-05T15:00"),
                   "dentist nov 5 2pm")
    assert err is None
    assert p["start"] == "2026-11-05T14:00:00-07:00"
    assert p["end"] == "2026-11-05T15:00:00-07:00"


def test_an_offset_from_the_model_keeps_the_local_wall_clock():
    """The prompt asks for local times. A "Z" the model tacked on was shown
    as 14:00 in the confirm but booked as 14:00 UTC — 8 AM here. The wall
    clock is what the user said, and the confirm must show what is booked."""
    p, err = valid(proposal(start="2026-11-05T14:00:00Z",
                            end="2026-11-05T15:00:00+00:00"),
                   "dentist nov 5 2pm")
    assert err is None
    assert p["start"] == "2026-11-05T14:00:00-07:00"
    assert p["end"] == "2026-11-05T15:00:00-07:00"
    when = dict(confirm_payload(p)["rows"])["When"]
    assert "14:00 – 15:00" in when


def test_missing_title_rejected():
    p, err = valid(proposal(title="  "))
    assert p is None and "title" in err


def test_unparseable_start_rejected():
    p, err = valid(proposal(start="tomorrowish"))
    assert p is None and "time" in err.lower()


def test_missing_end_defaults_to_thirty_minutes():
    p, err = valid(proposal(end=None))
    assert err is None and p["end"] == "2026-07-11T14:30:00-06:00"


def test_end_before_start_rejected():
    p, err = valid(proposal(end="2026-07-11T13:00"))
    assert p is None and "end" in err.lower()


def test_absurd_duration_rejected():
    p, err = valid(proposal(end="2026-07-12T09:00"))
    assert p is None and "long" in err.lower()


def test_past_start_rejected():
    p, err = valid(proposal(start="2026-07-10T09:00", end="2026-07-10T09:30"))
    assert p is None and "past" in err.lower()


def test_all_day_event_accepts_bare_dates():
    p, err = valid(proposal(start="2026-07-20", end=None, all_day=True))
    assert err is None
    assert p["all_day"] is True and p["start"] == "2026-07-20" and p["end"] == "2026-07-20"


def test_attendee_must_be_email_written_by_user():
    msg = "book a call with priya.nair@company.com friday 2pm"
    p, err = validate_proposal(proposal(attendees=["priya.nair@company.com"]),
                               now=NOW, user_message=msg)
    assert err is None and p["attendees"] == ["priya.nair@company.com"]
    # model invented an address the user never typed -> dropped, never
    # invited (the 4B reliably guesses one for "call with Chris"; a hard
    # refusal made every booking follow-up fail — live 2026-07-14)
    p, err = valid(proposal(attendees=["sam@guessed.com"]))
    assert err is None and p["attendees"] == []


def test_attendee_typed_in_booking_context_survives():
    p, err = validate_proposal(
        proposal(attendees=["priya.nair@company.com"]), now=NOW,
        user_message="book the first one",
        context='The user originally asked: "find 30 minutes with '
                'priya.nair@company.com"\nFree times...')
    assert err is None and p["attendees"] == ["priya.nair@company.com"]


def test_non_email_attendee_dropped():
    p, err = valid(proposal(attendees=["Sam"]))
    assert err is None and p["attendees"] == []


def test_recurrence_validated_and_kept_verbatim():
    p, err = valid(proposal(recurrence="RRULE:FREQ=WEEKLY;BYDAY=MO"))
    assert err is None and p["recurrence"] == "RRULE:FREQ=WEEKLY;BYDAY=MO"
    p, err = valid(proposal(recurrence="every monday forever"))
    assert p is None and "recurrence" in err.lower()


# ---- confirm payload ----

def test_confirm_payload_rows():
    p, _ = validate_proposal(
        proposal(location="Meet", recurrence="RRULE:FREQ=WEEKLY;BYDAY=MO",
                 attendees=["priya.nair@company.com"]),
        now=NOW,
        user_message="weekly call with priya.nair@company.com monday 2pm")
    pay = confirm_payload(p)
    rows = dict(pay["rows"])
    assert rows["Title"] == "Call with Sam"
    assert "Jul 11" in rows["When"] and "14:00" in rows["When"]
    assert rows["Calendar"] == "Personal (primary)"
    assert rows["Location"] == "Meet"
    assert "priya.nair@company.com" in rows["Attendees"]
    assert "invite" in rows["Attendees"].lower()          # emailing is stated plainly
    assert "RRULE:FREQ=WEEKLY;BYDAY=MO" in rows["Repeats"]  # verbatim, always
    assert pay["confirm_label"] == "Create event"


def test_confirm_payload_omits_empty_rows():
    p, _ = valid(proposal())
    rows = dict(confirm_payload(p)["rows"])
    assert "Location" not in rows and "Repeats" not in rows and "Attendees" not in rows


# ---- extraction pipeline ----

class FakeLLM:
    def __init__(self, text):
        self._text = text
        self.messages = None

    async def chat(self, messages):
        self.messages = messages
        yield self._text[: len(self._text) // 2]
        yield self._text[len(self._text) // 2:]


async def test_propose_event_happy_path():
    llm = FakeLLM('{"title": "Call with Sam", "start": "2026-07-11T14:00", '
                  '"end": "2026-07-11T14:30"}')
    p, err = await propose_event(llm, "book a call with Sam tomorrow 2pm", now=NOW)
    assert err is None and p["title"] == "Call with Sam"
    system = llm.messages[0]["content"]
    assert "2026-07-10" in system and "Friday" in system   # now + weekday in prompt


async def test_propose_event_unparseable_answer_is_honest():
    llm = FakeLLM("Sorry, no idea.")
    p, err = await propose_event(llm, "make an event", now=NOW)
    assert p is None and err


# ---- todo-fixes #8: deterministic dates override the model's arithmetic ----

from datetime import date

from lumen.daemon.llm.event_create import apply_resolved_date


def test_apply_resolved_date_shifts_start_and_end():
    raw = {"start": "2026-07-11T15:00", "end": "2026-07-11T15:30"}
    out = apply_resolved_date(raw, date(2026, 7, 15))
    assert out["start"] == "2026-07-15T15:00"
    assert out["end"] == "2026-07-15T15:30"


def test_apply_resolved_date_all_day_span_keeps_length():
    raw = {"start": "2026-07-11", "end": "2026-07-12"}
    out = apply_resolved_date(raw, date(2026, 7, 15))
    assert out == {"start": "2026-07-15", "end": "2026-07-16"}


def test_apply_resolved_date_garbage_untouched():
    raw = {"start": "whenever", "end": None}
    assert apply_resolved_date(raw, date(2026, 7, 15)) is raw


async def test_propose_event_overrides_wrong_relative_date():
    # NOW is Friday 2026-07-10, so "next wednesday" = 2026-07-15. The model
    # answered with the wrong day (the live bug) — code, not the 4B, owns
    # the calendar arithmetic.
    llm = FakeLLM('{"title": "Sync", "start": "2026-07-11T15:00", '
                  '"end": "2026-07-11T15:30"}')
    p, err = await propose_event(llm, "create a sync next wednesday at 3pm",
                                 now=NOW)
    assert err is None
    assert p["start"].startswith("2026-07-15T15:00")
    assert p["end"].startswith("2026-07-15T15:30")
    # and the resolved date was handed to the model up front
    assert "2026-07-15" in llm.messages[0]["content"]


async def test_propose_event_booking_context_skips_override():
    # A slot-booking follow-up carries exact dates in `context`; the resolver
    # must not second-guess them ("the tuesday slot" may be NEXT week's).
    llm = FakeLLM('{"title": "Call", "start": "2026-07-21T10:00", '
                  '"end": "2026-07-21T10:30"}')
    p, err = await propose_event(llm, "book the tuesday slot", now=NOW,
                                 context="- Tue 2026-07-21 10:00-10:30")
    assert err is None
    assert p["start"].startswith("2026-07-21T10:00")


# ---- year correction when no exact date was resolvable ----------------------

from lumen.daemon.llm.event_create import apply_year_correction


def test_year_correction_moves_next_year_off_the_current_year():
    raw = {"start": "2026-03-10T14:00", "end": "2026-03-10T15:00"}
    out = apply_year_correction(raw, "offsite in March next year", date(2026, 7, 19))
    assert out["start"] == "2027-03-10T14:00" and out["end"] == "2027-03-10T15:00"


def test_year_correction_rolls_a_past_month_forward():
    raw = {"start": "2026-03-10", "end": "2026-03-12", "all_day": True}
    out = apply_year_correction(raw, "the conference in March", date(2026, 7, 19))
    assert out["start"] == "2027-03-10" and out["end"] == "2027-03-12"


def test_year_correction_leaves_ordinary_proposals_alone():
    raw = {"start": "2026-07-20T09:00", "end": "2026-07-20T09:30"}
    assert apply_year_correction(raw, "standup tomorrow", date(2026, 7, 19)) is raw


# ---- todo-fixes #119: Google-shaped proposal fields ---------------------------

from lumen.daemon.llm.event_create import _coerce_datetime_field, _coerce_recurrence


def test_coerce_datetime_dict_extracts_datetime():
    assert _coerce_datetime_field({"dateTime": "2026-07-11T14:00:00Z"}) == "2026-07-11T14:00:00Z"


def test_coerce_datetime_dict_extracts_date():
    assert _coerce_datetime_field({"date": "2026-07-11"}) == "2026-07-11"


def test_coerce_datetime_string_passthrough():
    assert _coerce_datetime_field("2026-07-11T14:00") == "2026-07-11T14:00"


def test_coerce_recurrence_list_single_rrule():
    rec, err = _coerce_recurrence(["RRULE:FREQ=WEEKLY;BYDAY=MO"])
    assert rec == "RRULE:FREQ=WEEKLY;BYDAY=MO" and err is None


def test_coerce_recurrence_list_multiple_rules_rejected():
    _, err = _coerce_recurrence(["RRULE:FREQ=WEEKLY", "RRULE:FREQ=DAILY"])
    assert err is not None and "multiple" in err.lower()


def test_coerce_recurrence_list_non_rrule_rejected():
    _, err = _coerce_recurrence(["EXDATE:20260715"])
    assert err is not None


def test_coerce_recurrence_empty_list_is_none():
    rec, err = _coerce_recurrence([])
    assert rec is None and err is None


def test_coerce_recurrence_string_passthrough():
    rec, err = _coerce_recurrence("RRULE:FREQ=WEEKLY")
    assert rec == "RRULE:FREQ=WEEKLY" and err is None


def test_validate_proposal_google_dict_start_end():
    """Google's {'dateTime': ...} start/end must not crash (todo-fixes #119)."""
    p, err = valid(proposal(
        start={"dateTime": "2026-07-11T14:00:00-06:00"},
        end={"dateTime": "2026-07-11T14:30:00-06:00"}))
    assert err is None and p["title"] == "Call with Sam"


def test_validate_proposal_google_date_start_all_day():
    p, err = validate_proposal(
        {"title": "Holiday", "start": {"date": "2026-07-20"},
         "end": {"date": "2026-07-20"}, "all_day": True,
         "location": None, "description": None,
         "attendees": [], "recurrence": None},
        now=NOW, user_message="holiday july 20")
    assert err is None and p["all_day"] is True and p["start"] == "2026-07-20"


def test_validate_proposal_google_list_recurrence():
    p, err = valid(proposal(recurrence=["RRULE:FREQ=WEEKLY;BYDAY=MO"]))
    assert err is None and p["recurrence"] == "RRULE:FREQ=WEEKLY;BYDAY=MO"


def test_validate_proposal_google_multi_recurrence_rejected():
    p, err = valid(proposal(recurrence=["RRULE:FREQ=WEEKLY", "RRULE:FREQ=DAILY"]))
    assert p is None and "recurrence" in err.lower()


def test_validate_proposal_google_attendee_dicts():
    """Attendee objects from Google Calendar must not crash (todo-fixes #119)."""
    msg = "weekly call with priya.nair@company.com monday 2pm"
    p, err = validate_proposal(
        proposal(attendees=[{"email": "priya.nair@company.com",
                             "displayName": "Priya", "self": False}]),
        now=NOW, user_message=msg)
    assert err is None and "priya.nair@company.com" in p["attendees"]


def test_validate_proposal_google_location_dict():
    p, err = valid(proposal(location={"displayName": "Google Meet"}))
    assert err is None and p["location"] == "Google Meet"


# ---- todo-fixes #118: multi-date messages skip apply_resolved_date ----------

async def test_propose_event_multi_date_provides_reference_not_override():
    """'dinner tomorrow before the recital on friday' — two dates present.
    The model receives both as a reference but apply_resolved_date is skipped,
    so the model's own date choice survives."""
    # Model picks friday (the recital); since it's not overridden, the date stays.
    llm = FakeLLM('{"title": "Dinner", "start": "2026-07-17T19:00", '
                  '"end": "2026-07-17T20:00"}')
    # NOW is Friday 2026-07-10; tomorrow = Saturday, friday = today (July 10)
    p, err = await propose_event(
        llm,
        "dinner tomorrow before the recital on friday",
        now=NOW)
    assert err is None
    # System prompt carries both date refs but does NOT force a single date
    system = llm.messages[0]["content"]
    assert "tomorrow" in system.lower() and "friday" in system.lower()
    # "use exactly this date" is the single-date override hint — absent in multi-date
    assert "use exactly this date" not in system


async def test_propose_event_single_date_still_overrides():
    """Single resolvable date: apply_resolved_date still fires (regression guard)."""
    llm = FakeLLM('{"title": "Sync", "start": "2026-07-11T15:00", '
                  '"end": "2026-07-11T15:30"}')
    p, err = await propose_event(llm, "create a sync next wednesday at 3pm", now=NOW)
    assert err is None
    assert p["start"].startswith("2026-07-15T15:00")
    assert "use exactly this date" in llm.messages[0]["content"]
