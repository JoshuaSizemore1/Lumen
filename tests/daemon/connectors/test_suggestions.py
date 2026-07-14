"""SuggestionStore: pending/accept/dismiss lifecycle, per-email dedupe, and
the scan cursor. Suggestions live apart from todos so open_todos() (briefing,
chat context) never counts an unconfirmed extraction."""

from lumen.daemon import db
from lumen.daemon.connectors.suggestions import SuggestionStore


def make_store(tmp_path):
    return SuggestionStore(db.connect(tmp_path / "s.db"))


def add(store, **kw):
    args = {"text": "send the deck to Sam", "due_date": "2026-07-17",
            "quote": "I'll send that over Friday", "email_id": "m1",
            "subject": "Re: deck"}
    args.update(kw)
    return store.add(**args)


def test_add_and_pending_roundtrip(tmp_path):
    store = make_store(tmp_path)
    sid = add(store)
    rows = store.pending()
    assert rows[0]["id"] == sid and rows[0]["text"] == "send the deck to Sam"
    assert rows[0]["quote"] == "I'll send that over Friday"
    assert rows[0]["subject"] == "Re: deck"


def test_accept_and_dismiss_leave_pending(tmp_path):
    store = make_store(tmp_path)
    a = add(store, email_id="m1")
    b = add(store, email_id="m2", text="book flights")
    got = store.accept(a)
    assert got["text"] == "send the deck to Sam" and got["due_date"] == "2026-07-17"
    store.dismiss(b)
    assert store.pending() == []
    assert store.accept(999) is None      # unknown/expired id is harmless


def test_has_email_covers_every_status(tmp_path):
    # a dismissed or accepted email must never be re-suggested on re-scan
    store = make_store(tmp_path)
    sid = add(store, email_id="m1")
    store.dismiss(sid)
    assert store.has_email("m1") is True
    assert store.has_email("m2") is False


def test_scan_cursor_roundtrip(tmp_path):
    store = make_store(tmp_path)
    assert store.last_scan() is None
    store.set_last_scan("2026-07-13T10:00:00+00:00")
    assert store.last_scan() == "2026-07-13T10:00:00+00:00"
