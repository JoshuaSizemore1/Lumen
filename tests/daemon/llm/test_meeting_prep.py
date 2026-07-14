"""Meeting prep: deterministic event lookup + attendee mail history block,
one narration pass. Retrieval is daemon-side — the model summarizes what it
was handed, with subjects/dates so claims are checkable."""

from datetime import datetime

from lumen.daemon.llm.meeting_prep import (SYSTEM, build_prep_data,
                                           compose_prep, find_event)

NOW = datetime.fromisoformat("2026-07-13T08:00:00-06:00")

STANDUP = {"id": "e1", "calendar_id": "primary", "calendar_name": "Work",
           "color": "#7986cb", "title": "Team standup",
           "start_at": "2026-07-13T09:30:00-06:00",
           "end_at": "2026-07-13T10:00:00-06:00", "all_day": False,
           "location": "Meet", "description": "Daily sync",
           "attendees": [{"email": "priya@x.com", "name": "Priya", "self": False},
                         {"email": "me@x.com", "name": "Josh", "self": True}],
           "status": "confirmed"}
REVIEW = {"id": "e2", "calendar_id": "primary", "calendar_name": "Work",
          "color": "#7986cb", "title": "Budget review",
          "start_at": "2026-07-13T14:00:00-06:00",
          "end_at": "2026-07-13T15:00:00-06:00", "all_day": False,
          "location": None, "description": None,
          "attendees": [{"email": "sam@y.com", "name": "Sam Waters", "self": False}],
          "status": "confirmed"}
TOMORROW = dict(REVIEW, id="e3", title="Budget review",
                start_at="2026-07-14T14:00:00-06:00",
                end_at="2026-07-14T15:00:00-06:00")
NO_PEOPLE = {"id": "e4", "calendar_id": "primary", "calendar_name": "Personal",
             "color": "#333", "title": "Dentist", "start_at": "2026-07-13T11:00:00-06:00",
             "end_at": "2026-07-13T11:30:00-06:00", "all_day": False,
             "location": None, "description": None, "attendees": [],
             "status": "confirmed"}

EVENTS = [STANDUP, NO_PEOPLE, REVIEW, TOMORROW]


# ---- find_event ----

def test_finds_event_by_clock_time():
    assert find_event(EVENTS, "prep me for my 2pm", NOW) is REVIEW


def test_finds_event_by_clock_time_with_minutes():
    assert find_event(EVENTS, "prep me for the 9:30", NOW) is STANDUP


def test_time_match_prefers_soonest_occurrence():
    # two "2pm" events (today + tomorrow) — today's upcoming one wins
    assert find_event(EVENTS, "prep for my 2 pm meeting", NOW) is REVIEW


def test_finds_event_by_title_words():
    assert find_event(EVENTS, "prep me for the standup", NOW) is STANDUP
    assert find_event(EVENTS, "prepare me for the budget review", NOW) is REVIEW


def test_finds_event_by_attendee_name():
    assert find_event(EVENTS, "prep me for the meeting with Sam", NOW) is REVIEW


def test_no_match_returns_none():
    assert find_event(EVENTS, "prep me for the quarterly offsite", NOW) is None
    assert find_event([], "prep me for my 2pm", NOW) is None


# ---- build_prep_data ----

def history():
    return {"priya@x.com": [
        {"id": "m1", "sender": "Priya <priya@x.com>", "recipients": "me@x.com",
         "subject": "Sprint notes", "received_at": "2026-07-11T15:00:00+00:00"},
        {"id": "m2", "sender": "Josh <me@x.com>", "recipients": "priya@x.com",
         "subject": "Re: Sprint notes", "received_at": "2026-07-12T09:00:00+00:00"},
    ]}


def test_prep_data_has_event_and_history_lines():
    data = build_prep_data(STANDUP, history(), NOW)
    assert "Team standup" in data
    assert "09:30" in data and "Meet" in data
    assert "Daily sync" in data
    assert "Priya" in data
    assert "Sprint notes" in data and "2026-07-11" in data
    # direction is stated so the model can't flip who said what
    assert "from them" in data and "from you" in data


def test_prep_data_no_history_is_explicit():
    data = build_prep_data(REVIEW, {"sam@y.com": []}, NOW)
    assert "Sam Waters" in data
    assert "no email history" in data.lower()


def test_prep_data_without_mail_mirror_is_explicit():
    data = build_prep_data(REVIEW, None, NOW)
    assert "mirror" in data.lower() or "unavailable" in data.lower()


def test_prep_data_no_attendees_is_explicit():
    data = build_prep_data(NO_PEOPLE, {}, NOW)
    assert "no attendees" in data.lower()


def test_system_prompt_forbids_padding_and_demands_checkable_claims():
    low = SYSTEM.lower()
    assert "only" in low and ("shown" in low or "given" in low or "below" in low)
    assert "subject" in low
    assert "short" in low or "brief" in low


# ---- compose_prep ----

class FakeLLM:
    def __init__(self):
        self.messages = None

    async def chat(self, messages):
        self.messages = messages
        yield "Here's "
        yield "the brief."


async def test_compose_streams_and_carries_data():
    llm = FakeLLM()
    data = build_prep_data(STANDUP, history(), NOW)
    chunks = [c async for c in compose_prep(llm, data)]
    assert "".join(chunks) == "Here's the brief."
    assert llm.messages[0]["role"] == "system"
    assert llm.messages[0]["content"] == SYSTEM
    assert data in llm.messages[1]["content"]
