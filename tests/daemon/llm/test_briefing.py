"""Briefing assembly: deterministic sections the model narrates, honest
markers for empty/unavailable subsystems, one streaming compose pass."""

from datetime import datetime

from lumen.daemon.llm.briefing import SYSTEM, build_sections, compose_briefing

NOW = datetime.fromisoformat("2026-07-13T08:00:00-06:00")

EVENT = {"id": "e1", "calendar_id": "primary", "calendar_name": "Personal",
         "color": "#7986cb", "title": "Standup",
         "start_at": "2026-07-13T09:30:00-06:00",
         "end_at": "2026-07-13T10:00:00-06:00", "all_day": False,
         "location": "Meet", "description": None,
         "attendees": [], "status": "confirmed"}
ALLDAY = dict(EVENT, id="e2", title="PTO", start_at="2026-07-13",
              end_at="2026-07-14", all_day=True, location=None)

TODO_DUE = {"id": 1, "text": "buy milk", "due_date": "2026-07-13",
            "completed": False, "tags": ["errands"]}
TODO_OVERDUE = {"id": 2, "text": "send report", "due_date": "2026-07-10",
                "completed": False, "tags": []}
TODO_LATER = {"id": 3, "text": "later thing", "due_date": "2026-08-01",
              "completed": False, "tags": []}

UNREAD = [{"id": "m1", "sender": "Ada <a@x.com>", "subject": "Engines",
           "received_at": "2026-07-13T07:00:00+00:00"}]


def sections(**kw):
    args = {"events": [EVENT], "todos": [TODO_DUE, TODO_OVERDUE],
            "unread": UNREAD, "counts": {"total": 120, "unread": 1},
            "now": NOW, "cal_connected": True, "mail_connected": True,
            "mail_syncing": False}
    args.update(kw)
    return build_sections(**args)


def test_sections_have_all_three_headers_and_now_line():
    s = sections()
    assert "2026-07-13" in s and "Sunday" not in s   # 2026-07-13 is a Monday
    assert "Monday" in s
    for header in ("CALENDAR TODAY", "TODOS DUE", "UNREAD MAIL"):
        assert header in s


def test_events_formatted_timed_and_all_day():
    s = sections(events=[EVENT, ALLDAY])
    assert "09:30–10:00: Standup" in s and "at Meet" in s
    assert "all day: PTO" in s


def test_overdue_todos_flagged_distinctly_from_due_today():
    s = sections()
    assert "buy milk (due today) [errands]" in s
    assert "send report (OVERDUE since 2026-07-10)" in s


def test_unread_lines_and_counts():
    s = sections()
    assert "1 unread of 120 total" in s
    assert "Ada <a@x.com> — Engines" in s


def test_empty_markers_are_explicit():
    s = sections(events=[], todos=[], unread=[],
                 counts={"total": 120, "unread": 0})
    assert "No events today." in s
    assert "No todos due." in s
    assert "No unread mail." in s


def test_unavailable_subsystems_get_honest_markers():
    s = sections(events=[], cal_connected=False)
    assert "Calendar isn't connected" in s
    s = sections(unread=[], counts={"total": 0, "unread": 0},
                 mail_connected=False)
    assert "Gmail isn't connected" in s
    s = sections(mail_syncing=True)
    assert "sync" in s.lower()


def test_system_prompt_forbids_padding_and_invention():
    low = SYSTEM.lower()
    assert "only" in low and ("shown" in low or "given" in low or "below" in low)
    assert "short" in low or "brief" in low


class FakeLLM:
    def __init__(self):
        self.messages = None

    async def chat(self, messages):
        self.messages = messages
        yield "Good "
        yield "morning."


async def test_compose_streams_and_carries_sections():
    llm = FakeLLM()
    s = sections()
    chunks = [c async for c in compose_briefing(llm, s)]
    assert "".join(chunks) == "Good morning."
    assert llm.messages[0]["role"] == "system"
    assert llm.messages[0]["content"] == SYSTEM
    assert llm.messages[1]["role"] == "user"
    assert s in llm.messages[1]["content"]
