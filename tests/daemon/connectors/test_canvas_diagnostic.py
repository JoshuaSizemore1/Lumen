"""#58 — "the auto sync for canvas might be broken, it is not showing my new
classes."

The archive flag was the first suspect and it was wrong: `included` defaults to
1 in both the schema and the migration, so a newly discovered course is
included by default. The remaining suspect is upstream — `CanvasClient.courses`
only ever asks Canvas for `enrollment_state=active`, and a course whose term
has not started, or whose enrolment is still invited/pending, is not in that
set at all.

So this is a measurement before a change, which is the whole point: #58 has
already been guessed at once.
"""
import pytest

from lumen.daemon.connectors.canvas_sync import CanvasSync


class FakeStore:
    def __init__(self, mirror=()):
        self._mirror = list(mirror)

    def courses_for_panel(self):
        return list(self._mirror)


class FakeClient:
    def __init__(self, by_state, fail=()):
        self._by_state, self._fail = by_state, set(fail)
        self.asked = []

    def courses(self, state="active"):
        self.asked.append(state)
        if state in self._fail:
            raise RuntimeError("canvas said no")
        return list(self._by_state.get(state, []))


class Cfg:
    base_url = "https://utah.instructure.com"
    poll_minutes = 5
    enabled = True


def _sync(client, store, connected=True):
    s = CanvasSync(store, Cfg(), client_factory=lambda: client)
    s._session_alive = connected
    s._cookies = {"canvas_session": "x"} if connected else None
    return s


def course(cid, code):
    return {"id": cid, "name": f"{code} course", "course_code": code}


async def test_diagnostic_asks_every_enrolment_state():
    client = FakeClient({"active": [course(1, "CS3505")]})
    res = await _sync(client, FakeStore()).diagnose_courses()
    assert client.asked == list(CanvasSync.ENROLLMENT_STATES)
    assert res["counts"]["active"] == 1


async def test_a_next_term_class_is_named_as_missing_from_the_sync():
    """The shape #58 describes: enrolled, but invisible to the one query the
    poller makes."""
    client = FakeClient({"active": [course(1, "CS3505")],
                         "invited_or_pending": [course(2, "MATH2270")]})
    res = await _sync(client, FakeStore([course(1, "CS3505")])).diagnose_courses()
    assert [c["id"] for c in res["missing_from_active"]] == [2]
    assert res["not_mirrored"] == []


async def test_a_course_canvas_lists_but_lumen_never_stored_is_named_too():
    client = FakeClient({"active": [course(1, "CS3505"), course(9, "NEW101")]})
    res = await _sync(client, FakeStore([course(1, "CS3505")])).diagnose_courses()
    assert [c["id"] for c in res["not_mirrored"]] == [9]
    assert res["missing_from_active"] == []


async def test_a_course_in_two_states_is_not_reported_missing():
    client = FakeClient({"active": [course(1, "CS3505")],
                         "completed": [course(1, "CS3505")]})
    res = await _sync(client, FakeStore([course(1, "CS3505")])).diagnose_courses()
    assert res["missing_from_active"] == []


async def test_finished_courses_are_counted_not_accused():
    """The first live run against the real account returned 19 completed
    courses. Reporting those as classes the sync is hiding would have been
    worse than saying nothing — they are past terms, correctly excluded."""
    client = FakeClient({"active": [course(1, "CS4150")],
                         "completed": [course(7, "CS3505"), course(8, "MATH2270")]})
    res = await _sync(client, FakeStore([course(1, "CS4150")])).diagnose_courses()
    assert res["missing_from_active"] == []
    assert res["past"] == 2


async def test_a_course_the_user_archived_is_named():
    """Once the enrolment-state theory is measured and comes back empty, this
    is the likely real answer to "where did my class go"."""
    mirror = [dict(course(1, "CS4150"), included=1, active=1),
              dict(course(2, "CS5530"), included=0, active=1)]
    client = FakeClient({"active": [course(1, "CS4150"), course(2, "CS5530")]})
    res = await _sync(client, FakeStore(mirror)).diagnose_courses()
    assert [c["id"] for c in res["archived"]] == [2]


async def test_one_failing_state_does_not_lose_the_others():
    """A diagnostic that dies on the first error tells you less than one that
    reports three results and an error."""
    client = FakeClient({"active": [course(1, "CS3505")]},
                        fail=("completed",))
    res = await _sync(client, FakeStore()).diagnose_courses()
    assert res["counts"]["active"] == 1
    assert res["counts"]["completed"] == 0


async def test_disconnected_says_so_rather_than_reporting_nothing_found():
    res = await _sync(None, FakeStore(), connected=False).diagnose_courses()
    assert res == {"error": "not connected"}


# ---- #66 reconcile summary -------------------------------------------------
class CountingSync:
    """The reconcile_now wrapper reports whatever the pass recorded, plus the
    queue counts — the removals are the reason the action exists."""


async def test_reconcile_now_reports_what_the_pass_did(monkeypatch):
    class Queue:
        def count(self):
            return 2

    class Proposals:
        def count(self):
            return 1

    sync = _sync(None, FakeStore())
    sync._queue, sync._proposals = Queue(), Proposals()

    async def fake_sync_once():
        sync._report = {"courses": 3, "assignments": 12,
                        "todos": {"created": 4, "updated": 1},
                        "calendar": {"created": 2}}
        return True

    monkeypatch.setattr(sync, "sync_once", fake_sync_once)
    res = await sync.reconcile_now()
    assert res["ok"] is True
    assert res["todos"] == {"created": 4, "updated": 1}
    assert res["calendar"] == {"created": 2}
    assert res["queued_removals"] == 2 and res["proposals"] == 1


async def test_reconcile_now_reports_a_failed_pass_honestly(monkeypatch):
    sync = _sync(None, FakeStore())

    async def fake_sync_once():
        return False

    monkeypatch.setattr(sync, "sync_once", fake_sync_once)
    res = await sync.reconcile_now()
    assert res["ok"] is False
    assert res["queued_removals"] == 0
