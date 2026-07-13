"""gcal MCP server: input validation, graceful degradation, event formatting.
Fake service objects only — the live call is verified at the phase gate."""

from types import SimpleNamespace

from lumen.mcp_servers.gcal import NOT_CONNECTED, _list_events


class FakeExec:
    def __init__(self, payload):
        self._p = payload

    def execute(self):
        if isinstance(self._p, Exception):
            raise self._p
        return self._p


class FakeService:
    def __init__(self, cals, pages_by_cal):
        self._cals = cals
        self._pages = pages_by_cal

    def calendarList(self):
        return SimpleNamespace(list=lambda: FakeExec({"items": self._cals}))

    def events(self):
        return SimpleNamespace(list=lambda calendarId, pageToken=None, **kw: FakeExec(
            self._pages[calendarId][0 if pageToken is None else int(pageToken)]))


CALS = [{"id": "primary", "summary": "Personal"},
        {"id": "hideme", "summary": "Hidden", "hidden": True}]
ITEMS = {"items": [
    {"id": "t", "summary": "Dentist", "location": "Main St",
     "start": {"dateTime": "2026-09-14T15:00:00+02:00"},
     "end": {"dateTime": "2026-09-14T15:30:00+02:00"}},
    {"id": "a", "summary": "PTO", "start": {"date": "2026-09-20"},
     "end": {"date": "2026-09-21"}},
]}


def test_bad_dates_get_a_helpful_message():
    assert "ISO dates" in _list_events(FakeService(CALS, {}), "next month", "later")


def test_not_connected_message():
    assert _list_events(None, "2026-09-01", "2026-09-30") == NOT_CONNECTED


def test_formats_timed_and_all_day_and_skips_hidden():
    out = _list_events(FakeService(CALS, {"primary": [ITEMS]}),
                       "2026-09-01", "2026-09-30")
    assert "Dentist [Personal] at Main St" in out
    assert "2026-09-20 (all day): PTO" in out
    assert "Hidden" not in out


def test_api_failure_degrades_gracefully():
    svc = FakeService(CALS, {"primary": [RuntimeError("boom")]})
    assert "Couldn't reach" in _list_events(svc, "2026-09-01", "2026-09-30")


def test_no_events_is_an_explicit_answer():
    out = _list_events(FakeService([CALS[0]], {"primary": [{"items": []}]}),
                       "2026-09-01", "2026-09-30")
    assert "No events between 2026-09-01 and 2026-09-30" in out


# ---- create_event (write half) ----

from lumen.mcp_servers.gcal import _create_event


class InsertCapture:
    def __init__(self, fail=False):
        self.kwargs = None
        self._fail = fail

    def insert(self, **kwargs):
        self.kwargs = kwargs
        return FakeExec(RuntimeError("boom") if self._fail
                        else {"summary": kwargs["body"]["summary"],
                              "htmlLink": "https://cal/e1"})


class WriteService:
    def __init__(self, fail=False):
        self.api = InsertCapture(fail)

    def events(self):
        return self.api


def make_args(**kw):
    args = {"title": "Call with Sam", "start": "2026-07-11T14:00:00-06:00",
            "end": "2026-07-11T14:30:00-06:00", "all_day": False,
            "location": "", "description": "", "attendees": [], "recurrence": ""}
    args.update(kw)
    return args


def test_create_timed_event_no_attendees_sends_no_invites():
    svc = WriteService()
    out = _create_event(svc, **make_args())
    assert "Created: Call with Sam" in out and "https://cal/e1" in out
    assert svc.api.kwargs["calendarId"] == "primary"
    assert svc.api.kwargs["sendUpdates"] == "none"
    assert svc.api.kwargs["body"]["start"] == {"dateTime": "2026-07-11T14:00:00-06:00"}


def test_create_with_attendees_emails_invites():
    svc = WriteService()
    _create_event(svc, **make_args(attendees=["p@x.com"]))
    assert svc.api.kwargs["sendUpdates"] == "all"
    assert svc.api.kwargs["body"]["attendees"] == [{"email": "p@x.com"}]


def test_create_all_day_uses_exclusive_end_date():
    svc = WriteService()
    _create_event(svc, **make_args(start="2026-07-20", end="2026-07-20",
                                   all_day=True))
    assert svc.api.kwargs["body"]["start"] == {"date": "2026-07-20"}
    assert svc.api.kwargs["body"]["end"] == {"date": "2026-07-21"}   # Google: exclusive


def test_create_recurrence_gets_rrule_prefix_once():
    svc = WriteService()
    _create_event(svc, **make_args(recurrence="RRULE:FREQ=WEEKLY;BYDAY=MO"))
    assert svc.api.kwargs["body"]["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=MO"]
    _create_event(svc, **make_args(recurrence="FREQ=WEEKLY;BYDAY=MO"))
    assert svc.api.kwargs["body"]["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=MO"]


def test_create_not_connected_and_failure_degrade_gracefully():
    assert "isn't connected" in _create_event(None, **make_args())
    assert "Couldn't" in _create_event(WriteService(fail=True), **make_args())


# ---- delete_event (write half) ----

from lumen.mcp_servers.gcal import _delete_event


class DeleteCapture:
    def __init__(self, fail=False):
        self.kwargs = None
        self._fail = fail

    def delete(self, **kwargs):
        self.kwargs = kwargs
        return FakeExec(RuntimeError("boom") if self._fail else {})


class DeleteService:
    def __init__(self, fail=False):
        self.api = DeleteCapture(fail)

    def events(self):
        return self.api


def test_delete_event_reports_deleted_and_targets_the_right_calendar():
    svc = DeleteService()
    out = _delete_event(svc, "ev123", "work@group.calendar.google.com", False)
    assert out.startswith("Deleted")
    assert svc.api.kwargs == {"calendarId": "work@group.calendar.google.com",
                              "eventId": "ev123", "sendUpdates": "none"}


def test_delete_event_notifies_attendees_when_asked():
    svc = DeleteService()
    _delete_event(svc, "ev123", "primary", True)
    assert svc.api.kwargs["sendUpdates"] == "all"


def test_delete_event_needs_an_id():
    svc = DeleteService()
    assert "event_id" in _delete_event(svc, "", "primary", False)
    assert svc.api.kwargs is None


def test_delete_not_connected_and_failure_degrade_gracefully():
    assert "isn't connected" in _delete_event(None, "ev123", "primary", False)
    assert "Couldn't" in _delete_event(DeleteService(fail=True), "ev123",
                                       "primary", False)
