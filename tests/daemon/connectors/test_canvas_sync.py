import asyncio

import pytest

from lumen.daemon import db
from lumen.daemon.config import CanvasConfig
from lumen.daemon.connectors import canvas_session
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


class FlagLLM:
    async def chat(self, messages):
        yield '{"actionable": false, "text": "", "due": ""}'


async def test_sync_reconciles_assignments_into_todos(tmp_path):
    from lumen.daemon.connectors.todos import TodoStore
    conn = db.connect(tmp_path / "c.db")
    store = CanvasStore(conn)
    todos = TodoStore(conn)
    client = FakeClient(
        courses=[{"id": 1, "name": "CS 3505", "course_code": "CS3505"}],
        assignments={1: [{"id": 10, "course_id": 1, "name": "HW1",
                          "due_at": "2026-09-01T06:59:59Z", "points": 100.0,
                          "html_url": "u", "description": None, "submitted": False}]},
        announcements={1: [{"id": 5, "course_id": 1, "title": "Welcome",
                            "posted_at": "2026-08-20T00:00:00Z", "message": "hi",
                            "html_url": "a"}]})
    sync = CanvasSync(store, CanvasConfig(enabled=True), todos=todos, llm=FlagLLM(),
                      client_factory=lambda: client)
    sync.set_session({"canvas_session": "abc"})
    assert await sync.sync_once() is True
    assert [t["text"] for t in todos.list_all()] == ["CS3505 — HW1"]
    assert store.get_announcement(5)["actionable"] == 0
    assert sync.store is store


async def test_sync_without_todos_only_mirrors(tmp_path):
    client = FakeClient(courses=[{"id": 1, "name": "A", "course_code": "A"}])
    store, sync = make(tmp_path, client)
    assert await sync.sync_once() is True
    assert {c["id"] for c in store.active_courses()} == {1}


class RecordingClient(FakeClient):
    """Records which course ids assignments/announcements were fetched for."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.queried: list[int] = []

    def assignments(self, cid):
        self.queried.append(cid)
        return super().assignments(cid)

    def announcements(self, cid):
        return super().announcements(cid)


async def test_archived_course_is_not_fetched(tmp_path):
    courses = [{"id": 1, "name": "A", "course_code": "A"},
               {"id": 2, "name": "B", "course_code": "B"}]
    store, sync = make(tmp_path, FakeClient(courses=courses))
    await sync.sync_once()                       # first sync: both courses exist
    store.set_course_included(2, False)          # archive course 2

    rec = RecordingClient(courses=courses)
    sync._client_factory = lambda: rec
    await sync.sync_once()
    # Course 2 was archived, so the poller must not pull it — only course 1.
    assert rec.queried == [1]

    # Un-archive and it is pulled again.
    store.set_course_included(2, True)
    rec2 = RecordingClient(courses=courses)
    sync._client_factory = lambda: rec2
    await sync.sync_once()
    assert set(rec2.queried) == {1, 2}


# --- session persistence (Workstream P) --------------------------------------
def make_persistent(tmp_path, client, session_path=None):
    """Like make(), but with a session file — and NO session set, so each test
    decides whether one exists."""
    store = CanvasStore(db.connect(tmp_path / "p.db"))
    path = session_path or (tmp_path / "session.json")
    sync = CanvasSync(store, CanvasConfig(enabled=True), session_path=path,
                      client_factory=lambda: client)
    return store, sync, path


def test_set_session_persists_the_jar(tmp_path):
    _store, sync, path = make_persistent(tmp_path, FakeClient(courses=[]))
    sync.set_session({"canvas_session": "abc"})
    assert canvas_session.load(path) == {"canvas_session": "abc"}


def test_clear_session_removes_the_file(tmp_path):
    _store, sync, path = make_persistent(tmp_path, FakeClient(courses=[]))
    sync.set_session({"canvas_session": "abc"})
    sync.clear_session()
    assert not path.exists()
    assert sync.connected is False


def test_restore_session_reconnects_without_a_login(tmp_path):
    """The whole point: a daemon restart must not send the user back to Duo."""
    _store, sync, path = make_persistent(tmp_path, FakeClient(courses=[]))
    canvas_session.save(path, {"canvas_session": "abc"})
    assert sync.connected is False
    assert sync.restore_session() is True
    assert sync.connected is True


def test_restore_session_with_no_file_stays_disconnected(tmp_path):
    _store, sync, _path = make_persistent(tmp_path, FakeClient(courses=[]))
    assert sync.restore_session() is False
    assert sync.connected is False


def test_restore_session_is_a_noop_without_a_path(tmp_path):
    """Every pre-existing construction passes no session_path — unchanged."""
    store = CanvasStore(db.connect(tmp_path / "n.db"))
    sync = CanvasSync(store, CanvasConfig(enabled=True))
    assert sync.restore_session() is False
    sync.set_session({"canvas_session": "abc"})
    sync.clear_session()                       # must not raise


async def test_a_401_deletes_the_persisted_session(tmp_path):
    """The rule that makes persistence safe. Without it a dead cookie is
    restored on every restart forever: `connected` reports True until each first
    sync fails, and the UI never tells the user to log in again."""
    client = FakeClient(courses=[], raise_on="courses")
    _store, sync, path = make_persistent(tmp_path, client)
    sync.set_session({"canvas_session": "stale"})
    assert path.exists()
    assert await sync.sync_once() is False
    assert not path.exists()
    assert sync.connected is False
    assert sync.restore_session() is False     # and it stays gone across restarts


async def test_a_transient_error_KEEPS_the_persisted_session(tmp_path):
    """A flaky network is not a logout. Only a 401 may discard the session —
    otherwise one bad Wi-Fi moment costs a full CAS + Duo round trip."""

    class Flaky(FakeClient):
        def courses(self, state="active"):
            raise OSError("connection reset")

    _store, sync, path = make_persistent(tmp_path, Flaky(courses=[]))
    sync.set_session({"canvas_session": "good"})
    assert await sync.sync_once() is False
    assert path.exists()
    assert canvas_session.load(path) == {"canvas_session": "good"}
    assert sync.connected is True


async def test_per_course_401_keeps_the_session_and_skips_the_course(tmp_path):
    """todo-fixes #126: a per-course 401 is a Canvas permission error, not a
    session expiry. The session must remain alive and the other courses still
    sync. (The old test expected the session to die — that was the bug.)"""

    class PartialAccess(FakeClient):
        def assignments(self, cid):
            if cid == 1:
                raise CanvasSessionExpired("course 1 access denied")
            return list(self._assignments.get(cid, []))

    courses = [{"id": 1, "name": "Locked", "course_code": "LCK"},
               {"id": 2, "name": "Open", "course_code": "OPEN"}]
    client = PartialAccess(
        courses=courses,
        assignments={2: [{"id": 20, "course_id": 2, "name": "HW1",
                          "due_at": None, "points": None,
                          "html_url": "u", "description": None,
                          "submitted": False}]},
        announcements={1: [], 2: []})
    _store, sync, path = make_persistent(tmp_path, client)
    sync.set_session({"canvas_session": "good"})
    # Sync succeeds overall despite course 1's 401.
    assert await sync.sync_once() is True
    # Session is still alive — it was a per-course error, not an expiry.
    assert sync.connected is True
    assert path.exists()
    # Course 2's assignment was fetched normally.
    assert [a["name"] for a in _store.assignments(course_id=2)] == ["HW1"]
    assert sync._report["failed_courses"] == 1


async def test_aclose_stops_a_running_sync(tmp_path):
    """Shutdown: the shared sync task outlives the cancelled poller, so
    aclose ends it before the database closes under it (#129)."""
    import asyncio
    gate = asyncio.Event()
    _store, sync, _path = make_persistent(tmp_path, _client())
    sync.set_session({"canvas_session": "good"})

    async def stuck():
        await gate.wait()
        return True
    sync._do_sync = stuck
    waiter = asyncio.create_task(sync.sync_once())
    await asyncio.sleep(0)
    assert sync.busy
    await sync.aclose()
    assert not sync.busy
    waiter.cancel()


async def test_a_pass_where_no_course_answered_is_not_a_success(tmp_path):
    """#126: per-course errors are skipped — but when every course failed,
    nothing was pulled, and "Synced, up to date" would be a lie."""

    class Down(FakeClient):
        def assignments(self, cid):
            raise ConnectionError("network down")

    client = Down(courses=[{"id": 1, "name": "CS", "course_code": "CS"}],
                  assignments={}, announcements={1: []})
    _store, sync, _path = make_persistent(tmp_path, client)
    sync.set_session({"canvas_session": "good"})
    assert await sync.sync_once() is False
    assert sync.connected is True and sync.last_sync() is None


async def test_the_jar_that_just_401d_is_refused_on_the_way_back_in(tmp_path):
    """The live loop of 2026-08-27. A 401 dropped the session, the UI's
    loadAllCookies() replay handed the SAME dead cookies straight back,
    set_session marked them alive and re-persisted them, and the route kicked
    another sync — six 401s in one daemon run and a dead jar back on disk,
    surviving restarts because the file had been rewritten."""
    client = FakeClient(courses=[], raise_on="courses")
    _store, sync, path = make_persistent(tmp_path, client)
    sync.set_session({"canvas_session": "stale"})
    assert await sync.sync_once() is False
    assert sync.connected is False

    assert sync.set_session({"canvas_session": "stale"}) is False
    assert sync.connected is False
    assert not path.exists()               # and it is NOT re-persisted


async def test_a_genuinely_new_jar_is_accepted_after_a_dead_one(tmp_path):
    """The refusal must be jar-specific: a real re-login has to reconnect, or
    the fix would strand the user at the Connect hero forever."""
    client = FakeClient(courses=[], raise_on="courses")
    _store, sync, path = make_persistent(tmp_path, client)
    sync.set_session({"canvas_session": "stale"})
    assert await sync.sync_once() is False

    assert sync.set_session({"canvas_session": "fresh"}) is True
    assert sync.connected is True
    assert canvas_session.load(path) == {"canvas_session": "fresh"}


def test_an_empty_jar_is_never_a_session(tmp_path):
    """canvas.set_session falls back to {} when the payload carries no cookies.
    Accepting that reported `connected` True with nothing to authenticate
    with — a phantom connection the UI showed as a working login."""
    _store, sync, path = make_persistent(tmp_path, FakeClient(courses=[]))
    assert sync.set_session({}) is False
    assert sync.connected is False
    assert not path.exists()


# --- calendar sync inside the poll loop --------------------------------------
class FakeWriter:
    def __init__(self):
        self.created, self.patched, self.deleted = [], [], []

    def create_event(self, body):
        self.created.append(body)
        return f"evt_{len(self.created)}"

    def patch_event(self, event_id, body):
        self.patched.append(event_id)
        return True

    def delete_event(self, event_id):
        self.deleted.append(event_id)
        return True


def calendar_sync(tmp_path, client, *, sync_on=True, calendar=None):
    from lumen.daemon.connectors.canvas_alerts import CanvasAlerts
    from lumen.daemon.connectors.canvas_prefs import CanvasPrefs
    from lumen.daemon.connectors.canvas_queue import CanvasQueue
    conn = db.connect(tmp_path / "cal.db")
    store, prefs = CanvasStore(conn), CanvasPrefs(conn)
    prefs.set_sync_enabled(sync_on)
    queue, alerts, writer = CanvasQueue(conn), CanvasAlerts(conn), FakeWriter()
    sync = CanvasSync(store, CanvasConfig(enabled=True),
                      client_factory=lambda: client, markers=writer,
                      prefs=prefs, queue=queue, alerts=alerts,
                      calendar=calendar)
    sync.set_session({"canvas_session": "abc"})
    return store, sync, queue, writer


def _future(days: int) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _client(**over):
    return FakeClient(
        courses=[{"id": 1, "name": "CS 3505", "course_code": "CS3505"}],
        assignments={1: [{"id": 10, "course_id": 1, "name": "HW1",
                          "due_at": _future(7), "points": 100.0,
                          "html_url": "u", "description": None,
                          "submitted": False, **over}]},
        announcements={1: []})


async def test_a_fresh_sync_creates_events_silently(tmp_path):
    """No confirmation anywhere in this path — that is the agreed behaviour for
    create/update, and the reason removals need a different mechanism."""
    store, sync, queue, writer = calendar_sync(tmp_path, _client())
    assert await sync.sync_once() is True
    assert len(writer.created) == 1
    assert queue.count() == 0
    assert store.calendar_candidates()[0]["calendar_event_id"] == "evt_1"


async def test_the_switch_off_writes_nothing(tmp_path):
    """Merging this feature must not surprise-write to a real calendar."""
    _store, sync, queue, writer = calendar_sync(tmp_path, _client(), sync_on=False)
    assert await sync.sync_once() is True
    assert writer.created == [] and queue.count() == 0


async def test_paused_refuses_every_sync_not_just_the_timer(tmp_path):
    # todo-fixes #83: tab-open, Sync now and the sign-in hand-off all call
    # sync_once directly; Pause sync has to stop them there.
    def boom():
        raise AssertionError("no Canvas traffic while paused")

    store, sync, queue, writer = calendar_sync(tmp_path, _client())
    sync._client_factory = boom
    sync.paused = lambda: True
    assert await sync.sync_once() is False
    assert (await sync.reconcile_now())["ok"] is False
    assert sync.last_sync() is None and writer.created == []


async def test_calendar_paused_stops_canvas_calendar_writes(tmp_path):
    # todo-fixes #83: Google Calendar's own pause covers Canvas's writes to it.
    store, sync, queue, writer = calendar_sync(tmp_path, _client())
    sync.calendar_paused = lambda: True
    assert await sync.sync_once() is True                 # the mirror still syncs
    assert [a["name"] for a in store.assignments()] == ["HW1"]
    assert writer.created == []
    sync.calendar_paused = lambda: False                  # resumed: it catches up
    await sync.sync_once()
    assert len(writer.created) == 1


async def test_submitting_queues_a_removal_through_the_poll_loop(tmp_path):
    store, sync, queue, writer = calendar_sync(tmp_path, _client())
    await sync.sync_once()
    sync._client_factory = lambda: _client(submitted=True)
    assert await sync.sync_once() is True
    assert queue.count() == 1
    assert writer.deleted == []          # the loop only ever proposes


async def test_a_vanished_assignment_needs_two_syncs_to_count_as_gone(tmp_path):
    """One flaky fetch returning a short list must not read as a deleted term."""
    store, sync, queue, writer = calendar_sync(tmp_path, _client())
    await sync.sync_once()
    empty = FakeClient(courses=[{"id": 1, "name": "CS 3505", "course_code": "CS3505"}],
                       assignments={1: []}, announcements={1: []})
    sync._client_factory = lambda: empty
    await sync.sync_once()
    assert queue.count() == 0            # one miss is not gone
    await sync.sync_once()
    assert queue.count() == 1
    assert queue.pending()[0]["reason"] == "gone"


async def test_reappearing_resets_the_missing_streak(tmp_path):
    store, sync, _queue, _writer = calendar_sync(tmp_path, _client())
    await sync.sync_once()
    empty = FakeClient(courses=[{"id": 1, "name": "CS", "course_code": "CS"}],
                       assignments={1: []}, announcements={1: []})
    sync._client_factory = lambda: empty
    await sync.sync_once()
    assert store.calendar_candidates()[0]["missing_syncs"] == 1
    sync._client_factory = lambda: _client()
    await sync.sync_once()
    assert store.calendar_candidates()[0]["missing_syncs"] == 0


async def test_a_calendar_failure_does_not_fail_the_sync(tmp_path):
    """The mirror is the valuable part; a Google outage must not cost it."""
    class Boom(FakeWriter):
        def create_event(self, body):
            raise RuntimeError("google is down")

    store, sync, _queue, _writer = calendar_sync(tmp_path, _client())
    sync._markers = Boom()
    assert await sync.sync_once() is True
    assert [a["name"] for a in store.assignments()] == ["HW1"]


# --- #59: the calendar cache must see what the Canvas pass just wrote ---------
class FakeCalendarCache:
    """Stands in for CalendarSync — the local gcal mirror that the UI's
    `calendar.list` reads. Canvas writes go straight to Google through
    CalendarMarkerWriter, so this cache is the only thing standing between a
    newly-created assignment event and the calendar screen."""

    def __init__(self, ok=True):
        self.synced = 0
        self._ok = ok

    @property
    def busy(self):
        return False

    async def sync_once(self):
        self.synced += 1
        return self._ok


async def test_creating_canvas_events_refreshes_the_calendar_cache(tmp_path):
    """#59: Josh had to close Lumen entirely before new Canvas assignments
    showed on the calendar. The events really were on Google — but the daemon's
    event cache, which is all `calendar.list` ever reads, was only refreshed by
    CalendarSync's 5-minute tick and by daemon startup. A restart was simply the
    fastest way to force that refresh."""
    cal = FakeCalendarCache()
    _store, sync, _queue, writer = calendar_sync(tmp_path, _client(), calendar=cal)
    assert await sync.sync_once() is True
    assert len(writer.created) == 1
    assert cal.synced == 1


async def test_a_pass_that_writes_nothing_does_not_touch_google(tmp_path):
    """The refresh is a real Calendar API round trip. It is owed only to a pass
    that actually changed something — otherwise every idle poll tick would pull
    the whole window down again, on a laptop where that cost is the constraint."""
    cal = FakeCalendarCache()
    _store, sync, _queue, _writer = calendar_sync(tmp_path, _client(),
                                                  calendar=cal, sync_on=False)
    assert await sync.sync_once() is True
    assert cal.synced == 0


async def test_a_steady_state_resync_does_not_refresh_again(tmp_path):
    """First pass creates and refreshes; the second plans no actions at all, so
    it must stay silent. This is the common case — most poll ticks change
    nothing."""
    cal = FakeCalendarCache()
    _store, sync, _queue, _writer = calendar_sync(tmp_path, _client(), calendar=cal)
    await sync.sync_once()
    assert cal.synced == 1
    await sync.sync_once()
    assert cal.synced == 1


async def test_a_queued_removal_alone_refreshes_nothing(tmp_path):
    """Removals are only ever *proposed* — the loop never deletes from Google.
    Nothing changed there, so there is nothing to re-read."""
    cal = FakeCalendarCache()
    _store, sync, queue, writer = calendar_sync(tmp_path, _client(), calendar=cal)
    await sync.sync_once()
    cal.synced = 0
    sync._client_factory = lambda: _client(submitted=True)
    await sync.sync_once()
    assert queue.count() == 1
    assert writer.deleted == []
    assert cal.synced == 0


async def test_a_failed_calendar_refresh_never_fails_the_sync(tmp_path):
    """The Canvas mirror is the valuable part; a Google outage on the refresh
    must not cost it — same rule the write half already follows."""
    class Boom(FakeCalendarCache):
        async def sync_once(self):
            raise RuntimeError("google is down")

    _store, sync, _queue, writer = calendar_sync(tmp_path, _client(), calendar=Boom())
    assert await sync.sync_once() is True
    assert len(writer.created) == 1


async def test_model_paused_skips_ai_enrichment(tmp_path):
    # Canvas's "Lumen powered" passes are the other background model load; the
    # Settings switch turns them off the same way it turns off chat.
    from lumen.daemon.connectors.canvas_prefs import CanvasPrefs
    conn = db.connect(tmp_path / "e.db")
    store, prefs = CanvasStore(conn), CanvasPrefs(conn)
    prefs.set_sync_enabled(True)
    prefs.set_ai_mode(True)

    class BoomLLM:
        async def chat(self, messages):
            raise AssertionError("the model must not be touched while paused")
            yield ""

    sync = CanvasSync(store, CanvasConfig(enabled=True),
                      client_factory=lambda: _client(), prefs=prefs,
                      llm=BoomLLM())
    sync.model_paused = lambda: True
    assert await sync._enrich() == {}


async def test_exam_rescan_runs_once_in_the_next_enrichment_pass(tmp_path, monkeypatch):
    """#134: switching AI on asks for a re-scan of recent announcements; it
    runs inside the sync's own pass (so model-off applies), and only once."""
    from lumen.daemon.connectors.canvas_prefs import CanvasPrefs
    from lumen.daemon.connectors.canvas_proposals import CanvasProposals
    from lumen.daemon.llm import canvas_enrich
    conn = db.connect(tmp_path / "e.db")
    store, prefs = CanvasStore(conn), CanvasPrefs(conn)
    prefs.set_sync_enabled(True)
    prefs.set_ai_mode(True)
    calls = []

    async def rescan(*_a, **_k):
        calls.append(1)
        return {"proposed": 0}
    monkeypatch.setattr(canvas_enrich, "find_exam_dates_rescan", rescan)
    sync = CanvasSync(store, CanvasConfig(enabled=True),
                      client_factory=lambda: _client(), prefs=prefs,
                      llm=object(), proposals=CanvasProposals(conn))
    await sync._enrich()
    assert calls == []                      # not asked for
    sync.request_exam_rescan()
    sync.model_paused = lambda: True
    await sync._enrich()
    assert calls == [] and sync._exam_rescan  # model off: still owed
    sync.model_paused = lambda: False
    await sync._enrich()
    await sync._enrich()
    assert calls == [1] and not sync._exam_rescan


# --- #126: per-course 401 leaves session alive + errored course skipped -------

async def test_errored_course_is_excluded_from_mark_missing(tmp_path):
    """A course that errors out must not have its assignments accused of being
    gone — only successfully fetched courses may accrue the missing streak
    (todo-fixes #126)."""

    class FailingCourse(FakeClient):
        def assignments(self, cid):
            if cid == 2:
                raise OSError("network timeout")
            return list(self._assignments.get(cid, []))

    courses = [{"id": 1, "name": "A", "course_code": "A"},
               {"id": 2, "name": "B", "course_code": "B"}]
    asgn1 = {"id": 10, "course_id": 1, "name": "HW1", "due_at": None,
              "points": None, "html_url": "u", "description": None, "submitted": False}
    asgn2 = {"id": 20, "course_id": 2, "name": "HW2", "due_at": None,
              "points": None, "html_url": "u", "description": None, "submitted": False}
    client = FailingCourse(courses=courses,
                           assignments={1: [asgn1], 2: [asgn2]},
                           announcements={1: [], 2: []})
    store, sync = make(tmp_path, client)
    # Seed course 2's assignment via a healthy sync first.
    good = FakeClient(courses=courses, assignments={1: [asgn1], 2: [asgn2]},
                      announcements={1: [], 2: []})
    sync._client_factory = lambda: good
    await sync.sync_once()
    assert store.assignments(course_id=2)[0]["missing_syncs"] == 0

    # Next sync: course 2 errors → must NOT increment missing_syncs for course 2.
    sync._client_factory = lambda: client
    assert await sync.sync_once() is True    # sync still succeeds overall
    row2 = store.assignments(course_id=2)[0]
    assert row2["missing_syncs"] == 0        # NOT accused of being gone


# --- #128: set_session only kicks a sync on first connect ---------------------
# (Tested via CanvasSync.set_session + CanvasSync.connected state, since the
#  actual kick happens in the router. The sync's _inflight mechanism is the
#  observable signal here.)

async def test_coalesced_sync_once_returns_same_result(tmp_path):
    """todo-fixes #129: a second caller arriving mid-sync awaits the running
    task and gets the same True result — no 'already syncing' rejection."""
    started: list[int] = []

    class SlowClient(FakeClient):
        def courses(self, state="active"):
            started.append(1)
            return list(self._courses)

    client = SlowClient(
        courses=[{"id": 1, "name": "A", "course_code": "A"}],
        assignments={1: []}, announcements={1: []})
    store, sync = make(tmp_path, client)

    # Launch two sync_once calls concurrently.
    r1, r2 = await asyncio.gather(sync.sync_once(), sync.sync_once())
    assert r1 is True and r2 is True
    # Only one actual network fetch happened.
    assert len(started) == 1


async def test_reconcile_now_coalesces_with_running_sync(tmp_path):
    """todo-fixes #129: reconcile_now awaits an in-flight sync rather than
    returning 'already syncing'."""
    from lumen.daemon.connectors.todos import TodoStore

    conn = db.connect(tmp_path / "c.db")
    store, todos = CanvasStore(conn), TodoStore(conn)
    client = FakeClient(
        courses=[{"id": 1, "name": "CS", "course_code": "CS"}],
        assignments={1: [{"id": 10, "course_id": 1, "name": "HW1",
                          "due_at": "2026-11-01T06:59:59Z", "points": 10.0,
                          "html_url": "u", "description": None, "submitted": False}]},
        announcements={1: []})
    sync = CanvasSync(store, CanvasConfig(enabled=True), todos=todos,
                      client_factory=lambda: client)
    sync.set_session({"canvas_session": "abc"})

    # Both calls must succeed and not raise.
    r1, r2 = await asyncio.gather(sync.sync_once(), sync.reconcile_now())
    assert r1 is True
    assert r2["ok"] is True
    assert r2.get("assignments") == 1   # report from the real sync


# --- #137: announcement cutoff on classifier ----------------------------------

async def test_old_announcements_are_not_offered_to_the_classifier(tmp_path):
    """todo-fixes #137: only announcements within ANNOUNCEMENT_CUTOFF_DAYS are
    offered to the classifier — prevents 100+ model calls on first connect."""
    from datetime import datetime, timedelta, timezone
    from lumen.daemon.connectors.canvas_store import ANNOUNCEMENT_CUTOFF_DAYS

    store, sync = make(tmp_path, FakeClient(courses=[]))
    # An announcement posted well outside the cutoff window.
    old_date = (datetime.now(timezone.utc) - timedelta(days=ANNOUNCEMENT_CUTOFF_DAYS + 30)
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
    store.upsert_announcements([{"id": 1, "course_id": 1, "title": "Old exam info",
                                 "posted_at": old_date, "message": "m", "html_url": "a"}])
    # Old announcement: stored as actionable=0 AND filtered by cutoff query.
    assert store.unclassified_announcements(10) == []


async def test_recent_announcements_are_offered_to_the_classifier(tmp_path):
    """todo-fixes #137: announcements within the cutoff window still classify."""
    from datetime import datetime, timedelta, timezone
    from lumen.daemon.connectors.canvas_store import ANNOUNCEMENT_CUTOFF_DAYS

    store, sync = make(tmp_path, FakeClient(courses=[]))
    recent_date = (datetime.now(timezone.utc) - timedelta(days=3)
                   ).strftime("%Y-%m-%dT%H:%M:%SZ")
    store.upsert_announcements([{"id": 2, "course_id": 1, "title": "Quiz moved",
                                 "posted_at": recent_date, "message": "m", "html_url": "a"}])
    unclassified = store.unclassified_announcements(10)
    assert len(unclassified) == 1 and unclassified[0]["id"] == 2


async def test_old_announcement_inserted_as_non_actionable(tmp_path):
    """todo-fixes #137: upsert_announcements pre-classifies old rows as 0 so
    they never enter the classifier queue."""
    from datetime import datetime, timedelta, timezone
    from lumen.daemon.connectors.canvas_store import ANNOUNCEMENT_CUTOFF_DAYS

    store, sync = make(tmp_path, FakeClient(courses=[]))
    old_date = (datetime.now(timezone.utc) - timedelta(days=ANNOUNCEMENT_CUTOFF_DAYS + 5)
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
    store.upsert_announcements([{"id": 3, "course_id": 1, "title": "Term recap",
                                 "posted_at": old_date, "message": "m", "html_url": "a"}])
    ann = store.get_announcement(3)
    assert ann["actionable"] == 0   # pre-classified, not NULL
