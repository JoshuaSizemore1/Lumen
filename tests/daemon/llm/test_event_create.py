"""Event proposal extraction + the mechanical validation gate. The gate, not the
model, is what guarantees nothing malformed ever reaches a confirm dialog."""

from datetime import datetime, timedelta, timezone

from lumen.daemon.llm.event_create import (
    confirm_payload, parse_proposal, propose_event, validate_proposal,
)

TZ = timezone(timedelta(hours=-6))
NOW = datetime(2026, 7, 10, 14, 0, tzinfo=TZ)


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
    # model invented an address the user never typed -> rejected, named
    p, err = valid(proposal(attendees=["sam@guessed.com"]))
    assert p is None and "sam@guessed.com" in err


def test_non_email_attendee_rejected():
    p, err = valid(proposal(attendees=["Sam"]))
    assert p is None


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
