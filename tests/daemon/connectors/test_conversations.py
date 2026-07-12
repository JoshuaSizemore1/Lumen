import os
import stat

from lumen.daemon import db
from lumen.daemon.connectors.conversations import ConversationStore


def make_store(tmp_path):
    return ConversationStore(db.connect(tmp_path / "c.db"))


def test_create_derives_title_from_first_message(tmp_path):
    store = make_store(tmp_path)
    cid = store.create("what's on my calendar tomorrow?")
    assert isinstance(cid, int)
    convo = store.get(cid)["conversation"]
    assert convo["title"] == "what's on my calendar tomorrow?"
    assert convo["tool_engaged"] is False
    assert convo["created_at"] and convo["updated_at"]


def test_title_truncates_long_first_line(tmp_path):
    store = make_store(tmp_path)
    long = "please " * 40
    cid = store.create(long + "\nsecond line ignored")
    title = store.get(cid)["conversation"]["title"]
    assert len(title) <= 61 and "\n" not in title  # first line only, bounded


def test_add_message_and_history_in_order(tmp_path):
    store = make_store(tmp_path)
    cid = store.create("hi")
    store.add_message(cid, "user", "hi")
    store.add_message(cid, "assistant", "hello")
    store.add_message(cid, "user", "and my files?")
    assert store.history(cid) == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "and my files?"},
    ]


def test_history_can_cap_to_recent_turns(tmp_path):
    store = make_store(tmp_path)
    cid = store.create("q")
    for i in range(10):
        store.add_message(cid, "user", f"m{i}")
    tail = store.history(cid, limit=3)
    assert [m["content"] for m in tail] == ["m7", "m8", "m9"]  # most recent, still in order


def test_get_returns_full_messages_with_tool_calls(tmp_path):
    store = make_store(tmp_path)
    cid = store.create("find my resume")
    store.add_message(cid, "user", "find my resume")
    store.add_message(cid, "assistant", "found it", tool_calls=["list_directory"])
    got = store.get(cid)
    assert got["conversation"]["id"] == cid
    msgs = got["messages"]
    assert msgs[1]["tool_calls"] == ["list_directory"]
    assert msgs[0]["tool_calls"] is None


def test_list_recent_newest_first(tmp_path):
    store = make_store(tmp_path)
    a = store.create("first")
    b = store.create("second")
    store.add_message(b, "user", "bump b")  # b updated after a
    recent = store.list_recent()
    assert [r["id"] for r in recent][:2] == [b, a]
    assert set(recent[0]) == {"id", "title", "updated_at"}  # sidebar-shaped, no bodies


def test_tool_engaged_flag_roundtrips(tmp_path):
    store = make_store(tmp_path)
    cid = store.create("q")
    assert store.is_tool_engaged(cid) is False
    store.mark_tool_engaged(cid)
    assert store.is_tool_engaged(cid) is True


def test_deleting_conversation_cascades_messages(tmp_path):
    store = make_store(tmp_path)
    cid = store.create("q")
    store.add_message(cid, "user", "q")
    store.delete(cid)
    assert store.get(cid) is None
    assert store.list_recent() == []


def test_connect_tightens_db_file_to_600(tmp_path):
    path = tmp_path / "sensitive.db"
    db.connect(path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
