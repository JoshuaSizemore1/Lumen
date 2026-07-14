"""Commitment extraction: JSON parsing, the mechanical quote-grounding gate,
and the bounded forward-walking scan."""

from datetime import datetime

import pytest

from lumen.daemon import db
from lumen.daemon.connectors.suggestions import SuggestionStore
from lumen.daemon.llm.client import LLMUnavailable
from lumen.daemon.llm.commitments import parse_commitments, scan, validate

BODY = ("Hi Sam,\n\nGood catching up. I'll send the deck over Friday, "
        "and I will book the flights next week.\n\nJosh")


def test_parse_finds_json_array_in_noise():
    text = 'Here you go:\n[{"text": "send deck", "due": "", "quote": "x"}]\nDone.'
    assert parse_commitments(text)[0]["text"] == "send deck"
    assert parse_commitments("no json here") is None
    assert parse_commitments("[]") == []


def test_validate_requires_the_quote_verbatim_in_the_body():
    ok = validate([{"text": "send the deck to Sam", "due": "2026-07-17",
                    "quote": "I'll send the deck over Friday"}], BODY)
    assert ok == [{"text": "send the deck to Sam", "due_date": "2026-07-17",
                   "quote": "I'll send the deck over Friday"}]
    # invented quote -> dropped (grounding rule, same spirit as book recs)
    assert validate([{"text": "call the bank", "due": "",
                      "quote": "I promised to call the bank"}], BODY) == []


def test_validate_is_tolerant_of_case_and_whitespace():
    ok = validate([{"text": "book flights", "due": "",
                    "quote": "i WILL book   the flights"}], BODY)
    assert len(ok) == 1 and ok[0]["due_date"] is None


def test_validate_drops_empty_text_and_bad_dates():
    assert validate([{"text": "  ", "due": "", "quote": "I'll send the deck"}],
                    BODY) == []
    ok = validate([{"text": "send deck", "due": "friday-ish",
                    "quote": "I'll send the deck"}], BODY)
    assert ok[0]["due_date"] is None       # unparseable due degrades, item kept


class ScanLLM:
    """One canned reply per chat call."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = 0

    async def chat(self, messages):
        self.calls += 1
        reply = self._replies.pop(0)
        if reply is LLMUnavailable:
            raise LLMUnavailable("down")
        yield reply


def mail_store_with(rows):
    class FakeMail:
        def sent(self, since_iso, limit=20):
            return [r for r in rows if r["received_at"] > since_iso][:limit]
    return FakeMail()


def sent_mail(i, body=BODY):
    return {"id": f"s{i}", "subject": f"Re: thing {i}", "body": body,
            "received_at": f"2026-07-{i:02d}T09:00:00+00:00"}


NOW = datetime.fromisoformat("2026-07-13T10:00:00+00:00")
HIT = ('[{"text": "send the deck to Sam", "due": "2026-07-17", '
       '"quote": "I\'ll send the deck over Friday"}]')


async def test_scan_extracts_dedupes_and_advances_cursor(tmp_path):
    store = SuggestionStore(db.connect(tmp_path / "s.db"))
    llm = ScanLLM([HIT, "[]"])
    mail = mail_store_with([sent_mail(10), sent_mail(11)])
    result = await scan(llm, mail, store, now=NOW)
    assert result == {"scanned": 2, "found": 1}
    assert store.pending()[0]["email_id"] == "s10"
    assert store.last_scan() == "2026-07-11T09:00:00+00:00"
    # second scan: cursor is past both -> nothing scanned, LLM untouched
    result = await scan(llm, mail, store, now=NOW)
    assert result == {"scanned": 0, "found": 0} and llm.calls == 2


async def test_scan_skips_emails_already_suggested(tmp_path):
    store = SuggestionStore(db.connect(tmp_path / "s.db"))
    store.add("old", None, "q", "s10", "subj")     # s10 already handled
    llm = ScanLLM(["[]"])
    mail = mail_store_with([sent_mail(10), sent_mail(11)])
    result = await scan(llm, mail, store, now=NOW)
    assert result["scanned"] == 2 and llm.calls == 1   # s10 never reaches the LLM


async def test_scan_llm_down_propagates_and_keeps_cursor(tmp_path):
    store = SuggestionStore(db.connect(tmp_path / "s.db"))
    llm = ScanLLM([LLMUnavailable])
    mail = mail_store_with([sent_mail(10)])
    with pytest.raises(LLMUnavailable):
        await scan(llm, mail, store, now=NOW)
    assert store.last_scan() is None       # failed scan retries the same mail
