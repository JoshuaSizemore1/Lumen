"""Inbox triage: per-message verdicts, digest rendered from the mirror rows —
the model picks buckets but can never misname a sender or subject."""

from lumen.daemon.llm.triage import (SYSTEM, classify, message_line,
                                     parse_verdict, render_digest)

ROWS = [
    {"id": "m1", "sender": "Ada <a@x.com>", "subject": "Engines question",
     "snippet": "Could you send me the spec?", "is_read": False,
     "received_at": "2026-07-12T10:00:00+00:00"},
    {"id": "m2", "sender": "News <n@x.com>", "subject": "Weekly digest",
     "snippet": "Top stories this week", "is_read": False,
     "received_at": "2026-07-11T10:00:00+00:00"},
    {"id": "m3", "sender": "Sam <s@y.com>", "subject": "Lunch",
     "snippet": "Fun, see you there", "is_read": True,
     "received_at": "2026-07-10T10:00:00+00:00"},
]


def test_message_line_carries_state_date_sender_subject_snippet():
    line = message_line(ROWS[0])
    assert "[unread]" in line and "2026-07-12" in line
    assert "Ada <a@x.com>" in line and "Engines question" in line
    assert "Could you send me the spec?" in line
    assert "[read]" in message_line(ROWS[2])


def test_message_line_caps_snippets():
    line = message_line(dict(ROWS[0], snippet="x" * 500))
    assert "x" * 200 not in line


def test_parse_verdict_finds_json_in_prose_and_validates_bucket():
    assert parse_verdict(
        'Sure! {"bucket": "noise", "why": "newsletter"}') == ("noise", "newsletter")
    assert parse_verdict('{"bucket": "spam", "why": "w"}') is None
    assert parse_verdict("no json here") is None
    assert parse_verdict('{"bucket": "noise"}') == ("noise", "")


class FakeLLM:
    def __init__(self, reply):
        self.reply = reply
        self.messages = None

    async def chat(self, messages):
        self.messages = messages
        yield self.reply


async def test_classify_sends_one_message_and_parses_verdict():
    llm = FakeLLM('{"bucket": "needs_response", "why": "asks for the spec"}')
    verdict = await classify(llm, ROWS[0])
    assert verdict == ("needs_response", "asks for the spec")
    assert llm.messages[0]["content"] == SYSTEM
    assert "Engines question" in llm.messages[1]["content"]


async def test_classify_unparseable_is_none_not_a_guess():
    assert await classify(FakeLLM("you should reply to Ada"), ROWS[0]) is None


def test_render_names_come_from_rows_not_model():
    buckets = {"needs_response": [(ROWS[0], "asks for the spec")],
               "worth_reading": [], "noise": [(ROWS[1], "newsletter")]}
    text = render_digest(buckets, total=3)
    assert "Ada <a@x.com>" in text and "Engines question" in text
    assert "asks for the spec" in text
    assert "NEEDS A RESPONSE" in text and "NOISE" in text
    assert "nothing" in text.lower()               # empty bucket is explicit
    assert "didn't categorize 1" in text           # m3 left out — said honestly


def test_system_prompt_demands_json_and_defines_buckets():
    low = SYSTEM.lower()
    assert "json" in low and "never needs_response" in low
