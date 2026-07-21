import asyncio

import pytest

from lumen.daemon import db
from lumen.daemon.config import CanvasConfig
from lumen.daemon.connectors.canvas_client import CanvasSessionExpired
from lumen.daemon.connectors.canvas_store import CanvasStore
from lumen.daemon.connectors.canvas_sync import CanvasSync


class FakeClient:
    """Stands in for CanvasClient: returns canned data, records close()."""

    def __init__(self, courses, assignments=None, announcements=None, raise_on=None):
        self._courses = courses
        self._assignments = assignments or {}
        self._announcements = announcements or {}
        self._raise_on = raise_on
        self.closed = False

    def courses(self, state="active"):
        if self._raise_on == "courses":
            raise CanvasSessionExpired("courses")
        return list(self._courses)

    def assignments(self, cid):
        return list(self._assignments.get(cid, []))

    def announcements(self, cid):
        return list(self._announcements.get(cid, []))

    def close(self):
        self.closed = True


def make(tmp_path, client, cfg=None):
    store = CanvasStore(db.connect(tmp_path / "c.db"))
    sync = CanvasSync(store, cfg or CanvasConfig(enabled=True, poll_minutes=45),
                      client_factory=lambda: client)
    sync.set_session({"canvas_session": "abc"})
    return store, sync


async def test_sync_populates_mirror(tmp_path):
    client = FakeClient(
        courses=[{"id": 1, "name": "CS 3505", "course_code": "CS3505"}],
        assignments={1: [{"id": 10, "course_id": 1, "name": "HW1",
                          "due_at": "2026-09-01T06:59:59Z", "points": 100.0,
                          "html_url": "u", "description": None, "submitted": False}]},
        announcements={1: [{"id": 5, "course_id": 1, "title": "Welcome",
                            "posted_at": "2026-08-20T00:00:00Z", "message": "hi",
                            "html_url": "a"}]})
    store, sync = make(tmp_path, client)
    assert await sync.sync_once() is True
    assert {c["id"] for c in store.active_courses()} == {1}
    assert [a["name"] for a in store.assignments()] == ["HW1"]
    assert [a["title"] for a in store.announcements()] == ["Welcome"]
    assert sync.last_sync() is not None
    assert client.closed is True


async def test_sync_no_session_is_noop(tmp_path):
    store = CanvasStore(db.connect(tmp_path / "c.db"))
    sync = CanvasSync(store, CanvasConfig(enabled=True),
                      client_factory=lambda: FakeClient(courses=[]))
    # set_session was never called — no cookies, not connected.
    assert sync.connected is False
    assert await sync.sync_once() is False
    assert store.active_courses() == []


async def test_session_expiry_marks_disconnected(tmp_path):
    client = FakeClient(courses=[], raise_on="courses")
    store, sync = make(tmp_path, client)
    assert sync.connected is True
    assert await sync.sync_once() is False
    assert sync.connected is False          # the 401 flipped it — UI must re-login
    assert client.closed is True


async def test_sync_deactivates_dropped_courses(tmp_path):
    store, sync = make(tmp_path, FakeClient(
        courses=[{"id": 1, "name": "A", "course_code": "A"},
                 {"id": 2, "name": "B", "course_code": "B"}]))
    await sync.sync_once()
    assert {c["id"] for c in store.active_courses()} == {1, 2}
    # Next term: only course 2 is still active.
    sync._client_factory = lambda: FakeClient(
        courses=[{"id": 2, "name": "B", "course_code": "B"}])
    await sync.sync_once()
    assert {c["id"] for c in store.active_courses()} == {2}


async def test_poll_forever_runs_a_sync_then_cancels_cleanly(tmp_path):
    client = FakeClient(courses=[{"id": 1, "name": "A", "course_code": "A"}])
    store, sync = make(tmp_path, client,
                       cfg=CanvasConfig(enabled=True, poll_minutes=5))
    task = asyncio.create_task(sync.poll_forever())
    for _ in range(100):                     # wait up to ~2s for the first sync
        if sync.last_sync() is not None:
            break
        await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sync.last_sync() is not None
    assert {c["id"] for c in store.active_courses()} == {1}
