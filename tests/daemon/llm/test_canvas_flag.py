import json

from lumen.daemon import db
from lumen.daemon.connectors.canvas_store import CanvasStore
from lumen.daemon.llm import canvas_flag


class FakeLLM:
    """Yields a scripted reply per call, in order."""
    def __init__(self, replies):
        self._replies = list(replies)

    async def chat(self, messages):
        yield self._replies.pop(0)


def test_parse_and_validate_actionable():
    obj = canvas_flag.parse_flag(
        'sure: {"actionable": true, "text": "Study ch 4", "due": "2026-08-28"} ok')
    v = canvas_flag.validate_flag(obj)
    assert v == {"actionable": True, "text": "Study ch 4", "due": "2026-08-28"}


def test_validate_non_actionable_clears_fields():
    v = canvas_flag.validate_flag({"actionable": False, "text": "x", "due": "2026-01-01"})
    assert v == {"actionable": False, "text": "", "due": None}


def test_validate_bad_due_degrades_to_none():
    v = canvas_flag.validate_flag({"actionable": True, "text": "do it", "due": "someday"})
    assert v["due"] is None and v["actionable"] is True


async def test_flag_announcements_writes_flags(tmp_path):
    store = CanvasStore(db.connect(tmp_path / "c.db"))
    store.upsert_courses([{"id": 1, "name": "CS", "course_code": "CS"}])
    store.upsert_announcements([
        {"id": 5, "course_id": 1, "title": "Exam Friday", "posted_at": "2026-08-20T00:00:00Z",
         "message": "midterm Friday", "html_url": "a"},
        {"id": 6, "course_id": 1, "title": "Welcome", "posted_at": "2026-08-19T00:00:00Z",
         "message": "hi all", "html_url": "b"}])
    llm = FakeLLM(['{"actionable": true, "text": "Study for midterm", "due": "2026-08-28"}',
                   '{"actionable": false, "text": "", "due": ""}'])
    res = await canvas_flag.flag_announcements(store, llm)
    assert res == {"flagged": 2, "actionable": 1}
    a5 = store.get_announcement(5)
    assert a5["actionable"] == 1 and a5["seen"] == 1
    assert json.loads(a5["suggested_todo"])["text"] == "Study for midterm"
    assert store.get_announcement(6)["actionable"] == 0
    assert store.unclassified_announcements(10) == []


async def test_flag_is_bounded_by_cap(tmp_path):
    store = CanvasStore(db.connect(tmp_path / "c.db"))
    store.upsert_courses([{"id": 1, "name": "CS", "course_code": "CS"}])
    store.upsert_announcements([
        {"id": i, "course_id": 1, "title": f"A{i}", "posted_at": f"2026-08-{i:02d}T00:00:00Z",
         "message": "m", "html_url": "u"} for i in range(1, 6)])
    llm = FakeLLM(['{"actionable": false, "text": "", "due": ""}'] * 2)
    res = await canvas_flag.flag_announcements(store, llm, cap=2)
    assert res["flagged"] == 2
    assert len(store.unclassified_announcements(10)) == 3
