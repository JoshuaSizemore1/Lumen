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
