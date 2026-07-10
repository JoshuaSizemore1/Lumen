"""BookStore CRUD + the catalog context injected into recommendation prompts."""

import pytest

from lumen.daemon import db
from lumen.daemon.connectors.books import BookStore


def make_store(tmp_path):
    return BookStore(db.connect(tmp_path / "b.db"))


def test_add_returns_full_list_and_defaults_date_to_today(tmp_path):
    store = make_store(tmp_path)
    rows = store.add("Piranesi", "Susanna Clarke", 4, "quiet, strange")
    assert len(rows) == 1
    b = rows[0]
    assert b["title"] == "Piranesi" and b["author"] == "Susanna Clarke"
    assert b["rating"] == 4 and b["notes"] == "quiet, strange"
    assert b["date_finished"]  # stamped today by default
    assert b["tags"] == []


def test_add_optional_fields_default_to_none(tmp_path):
    b = make_store(tmp_path).add("Dune")[0]
    assert b["author"] is None and b["rating"] is None and b["notes"] is None


def test_add_empty_title_raises_and_inserts_nothing(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(ValueError):
        store.add("   ")
    assert store.list_all() == []


def test_add_rating_out_of_range_raises(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(ValueError):
        store.add("Dune", rating=0)
    with pytest.raises(ValueError):
        store.add("Dune", rating=6)


def test_list_all_newest_first_by_date_finished(tmp_path):
    store = make_store(tmp_path)
    store.add("Old", date_finished="2026-05-01")
    store.add("New", date_finished="2026-07-01")
    assert [b["title"] for b in store.list_all()] == ["New", "Old"]


def test_delete_returns_fresh_list(tmp_path):
    store = make_store(tmp_path)
    bid = store.add("Dune", date_finished="2026-05-01")[0]["id"]
    rows = store.delete(bid)
    assert rows == []


def test_catalog_context_lists_entries(tmp_path):
    store = make_store(tmp_path)
    store.add("Piranesi", "Susanna Clarke", 4, "quiet, strange",
              date_finished="2026-04-30")
    ctx = store.catalog_context()
    assert "reading log" in ctx
    assert "- Piranesi by Susanna Clarke — rated 4/5 — notes: quiet, strange" in ctx


def test_catalog_context_empty_marker(tmp_path):
    assert "empty" in make_store(tmp_path).catalog_context()
