import json
from datetime import date, timedelta

from lumen.daemon import db
from lumen.daemon.connectors.canvas_store import CanvasStore
from lumen.daemon.llm import canvas_flag


class FakeLLM:
    """Yields a scripted reply per call, in order."""
    def __init__(self, replies):
        self._replies = list(replies)
        self.last_messages = None

    async def chat(self, messages):
        self.last_messages = messages
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


def test_validate_past_due_date_degrades_to_none():
    """todo-fixes #133: a due date already in the past is not an upcoming
    deadline — drop it rather than suggest an already-missed task."""
    today = date(2026, 10, 11)
    v = canvas_flag.validate_flag(
        {"actionable": True, "text": "Submit it", "due": "2026-01-01"},
        today=today)
    assert v["due"] is None and v["actionable"] is True


async def test_classify_strips_html_before_capping():
    """todo-fixes #133: a due date near the end of the body must survive the
    BODY_CAP cut — the cap is on plain text, not raw HTML.  A simple sanity
    check: the prompt the model sees must not contain raw HTML tags."""
    llm = FakeLLM(['{"actionable": false, "text": "", "due": ""}'])
    html_body = "<p>Please <b>read</b> chapter 3. <em>Due Friday.</em></p>"
    await canvas_flag.classify(llm, {
        "title": "Reading",
        "posted_at": "2026-10-01T00:00:00Z",
        "message": html_body,
    })
    user_msg = llm.last_messages[1]["content"]
    assert "<p>" not in user_msg and "<b>" not in user_msg
    assert "chapter 3" in user_msg


async def test_classify_prompt_includes_today_and_posted_dates():
    """todo-fixes #133: year-less relative dates like 'Thursday Oct 16' need
    context to resolve to the right year.  Both today and posted_at must be in
    the system message."""
    llm = FakeLLM(['{"actionable": false, "text": "", "due": ""}'])
    await canvas_flag.classify(llm, {
        "title": "T",
        "posted_at": "2026-09-15T00:00:00Z",
        "message": "some text",
    })
    sys_msg = llm.last_messages[0]["content"]
    assert "2026-09-15" in sys_msg     # posted date
    assert str(date.today().year) in sys_msg   # today's year at minimum


def _days_ago(n: int) -> str:
    """Relative to today: only recent announcements reach the classifier
    (#137), so fixed dates here would rot."""
    return f"{(date.today() - timedelta(days=n)).isoformat()}T00:00:00Z"


async def test_flag_announcements_writes_flags(tmp_path):
    store = CanvasStore(db.connect(tmp_path / "c.db"))
    store.upsert_courses([{"id": 1, "name": "CS", "course_code": "CS"}])
    store.upsert_announcements([
        {"id": 5, "course_id": 1, "title": "Exam Friday", "posted_at": _days_ago(2),
         "message": "midterm Friday", "html_url": "a"},
        {"id": 6, "course_id": 1, "title": "Welcome", "posted_at": _days_ago(3),
         "message": "hi all", "html_url": "b"}])
    due = (date.today() + timedelta(days=5)).isoformat()
    llm = FakeLLM([f'{{"actionable": true, "text": "Study for midterm", "due": "{due}"}}',
                   '{"actionable": false, "text": "", "due": ""}'])
    res = await canvas_flag.flag_announcements(store, llm)
    assert res == {"flagged": 2, "actionable": 1}
    a5 = store.get_announcement(5)
    assert a5["actionable"] == 1 and a5["seen"] == 1
    assert json.loads(a5["suggested_todo"]) == {"text": "Study for midterm", "due": due}
    assert store.get_announcement(6)["actionable"] == 0
    assert store.unclassified_announcements(10) == []


async def test_flag_is_bounded_by_cap(tmp_path):
    store = CanvasStore(db.connect(tmp_path / "c.db"))
    store.upsert_courses([{"id": 1, "name": "CS", "course_code": "CS"}])
    store.upsert_announcements([
        {"id": i, "course_id": 1, "title": f"A{i}", "posted_at": _days_ago(i),
         "message": "m", "html_url": "u"} for i in range(1, 6)])
    llm = FakeLLM(['{"actionable": false, "text": "", "due": ""}'] * 2)
    res = await canvas_flag.flag_announcements(store, llm, cap=2)
    assert res["flagged"] == 2
    assert len(store.unclassified_announcements(10)) == 3
