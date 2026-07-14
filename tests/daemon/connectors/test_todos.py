from datetime import date

import pytest

from lumen.daemon import db
from lumen.daemon.connectors.todos import TodoStore

TODAY = date(2026, 7, 8)


def make_store(tmp_path):
    return TodoStore(db.connect(tmp_path / "t.db"))


def test_add_parses_and_returns_full_list(tmp_path):
    store = make_store(tmp_path)
    rows = store.add("renew domain @jul9 #admin #web", today=TODAY)
    assert len(rows) == 1
    t = rows[0]
    assert t["text"] == "renew domain"
    assert t["due_date"] == "2026-07-09"
    assert t["tags"] == ["admin", "web"]
    assert t["completed"] is False and t["source"] == "manual"
    assert t["created_at"]  # set, exact value not pinned


def test_add_empty_text_raises_and_inserts_nothing(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(ValueError):
        store.add("#home @today", today=TODAY)
    with pytest.raises(ValueError):
        store.add("   ", today=TODAY)
    assert store.list_all() == []


def test_toggle_and_delete_return_fresh_list(tmp_path):
    store = make_store(tmp_path)
    tid = store.add("a", today=TODAY)[0]["id"]
    assert store.toggle(tid, True)[0]["completed"] is True
    assert store.toggle(tid, False)[0]["completed"] is False
    assert store.delete(tid) == []


def test_toggle_and_delete_unknown_id_are_noops(tmp_path):
    store = make_store(tmp_path)
    store.add("a", today=TODAY)
    assert store.toggle(999, True)[0]["completed"] is False
    assert len(store.delete(999)) == 1


def test_list_all_keeps_insertion_order(tmp_path):
    store = make_store(tmp_path)
    store.add("first", today=TODAY)
    store.add("second", today=TODAY)
    assert [t["text"] for t in store.list_all()] == ["first", "second"]


def test_open_todos_excludes_completed_and_orders_by_due(tmp_path):
    store = make_store(tmp_path)
    store.add("no date", today=TODAY)
    store.add("later @2026-07-20", today=TODAY)
    store.add("sooner @2026-07-10", today=TODAY)
    done_id = store.add("done @today", today=TODAY)[-1]["id"]
    store.toggle(done_id, True)
    assert [t["text"] for t in store.open_todos()] == ["sooner", "later", "no date"]


def test_add_with_source_records_it(tmp_path):
    from lumen.daemon import db
    from lumen.daemon.connectors.todos import TodoStore
    store = TodoStore(db.connect(tmp_path / "src.db"))
    rows = store.add("send the deck @2026-07-17", source="llm-extracted")
    assert rows[-1]["source"] == "llm-extracted"
    rows = store.add("plain one")
    assert rows[-1]["source"] == "manual"
