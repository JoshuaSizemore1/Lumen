from datetime import datetime, timedelta

from lumen.daemon import db
from lumen.daemon.connectors.memory_log import MemoryLog


def store(tmp_path):
    return MemoryLog(db.connect(tmp_path / "m.db"))


def test_log_and_unfolded(tmp_path):
    m = store(tmp_path)
    m.log("todos", "query", {"message": "what's due"})
    m.log("calendar", "correction", {"message": "no, tuesday"})
    rows = m.unfolded()
    assert m.unfolded_count() == 2
    assert rows[0]["subsystem"] == "todos"
    assert rows[0]["detail"] == {"message": "what's due"}
    assert rows[1]["kind"] == "correction"


def test_bad_kind_swallowed(tmp_path):
    m = store(tmp_path)
    m.log("todos", "bogus", {"x": 1})   # must not raise
    assert m.unfolded_count() == 0


def test_oldest_age_and_recent(tmp_path):
    m = store(tmp_path)
    now = datetime(2026, 7, 14, 12, 0, 0)
    m._conn.execute(
        "INSERT INTO memory_log (ts, subsystem, kind, detail) VALUES (?,?,?,?)",
        ((now - timedelta(hours=30)).isoformat(), "books", "query", "{}"))
    m._conn.commit()
    assert m.oldest_unfolded_age_hours(now) >= 29.9
    assert m.recent_within("books", "query", 3600 * 40, now) is True
    assert m.recent_within("books", "query", 3600, now) is False


def test_mark_folded_and_prune(tmp_path):
    m = store(tmp_path)
    m.log("todos", "query", {"a": 1})
    ids = [r["id"] for r in m.unfolded()]
    m.mark_folded(ids)
    assert m.unfolded_count() == 0
    old = (datetime.now() - timedelta(days=40)).isoformat()
    m._conn.execute("UPDATE memory_log SET ts = ? WHERE id = ?", (old, ids[0]))
    m._conn.commit()
    m.prune_folded((datetime.now() - timedelta(days=30)).isoformat())
    assert m._conn.execute("SELECT COUNT(*) c FROM memory_log").fetchone()["c"] == 0


def test_delete_matching(tmp_path):
    m = store(tmp_path)
    m.log("email", "query", {"message": "archive PulteGroup newsletter"})
    m.log("todos", "query", {"message": "buy milk"})
    assert m.delete_matching("pultegroup") == 1
    assert m.unfolded_count() == 1


def test_last_run_roundtrip(tmp_path):
    m = store(tmp_path)
    assert m.last_run() is None
    m.set_last_run("2026-07-14T12:00:00")
    assert m.last_run() == "2026-07-14T12:00:00"
