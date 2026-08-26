from lumen.daemon.connectors.gcal import CalendarMarkerWriter


class _Exec:
    def __init__(self, ret):
        self._ret = ret

    def execute(self):
        return self._ret


class FakeEvents:
    def __init__(self, sink):
        self._sink = sink

    def insert(self, calendarId, body, sendUpdates="none"):
        self._sink["insert"] = {"cal": calendarId, "body": body}
        return _Exec({"id": "evt_new"})

    def patch(self, calendarId, eventId, body, sendUpdates="none"):
        self._sink["patch"] = {"cal": calendarId, "id": eventId, "body": body}
        return _Exec({"id": eventId})


class FakeService:
    def __init__(self, sink):
        self._sink = sink

    def events(self):
        return FakeEvents(self._sink)


def test_create_all_day_returns_id_and_exclusive_end():
    sink = {}
    w = CalendarMarkerWriter(google_cfg=None, service_factory=lambda: FakeService(sink))
    eid = w.create_all_day("HW1 due", "2026-09-01")
    assert eid == "evt_new"
    assert sink["insert"]["body"]["start"] == {"date": "2026-09-01"}
    assert sink["insert"]["body"]["end"] == {"date": "2026-09-02"}   # exclusive


def test_create_all_day_none_when_not_connected():
    w = CalendarMarkerWriter(google_cfg=None, service_factory=lambda: None)
    assert w.create_all_day("x", "2026-09-01") is None


def test_patch_all_day_moves_marker():
    sink = {}
    w = CalendarMarkerWriter(google_cfg=None, service_factory=lambda: FakeService(sink))
    assert w.patch_all_day("evt_1", "2026-09-05") is True
    assert sink["patch"]["id"] == "evt_1"
    assert sink["patch"]["body"]["start"] == {"date": "2026-09-05"}
    assert sink["patch"]["body"]["end"] == {"date": "2026-09-06"}


# --- Canvas -> Calendar event writes -----------------------------------------
import pytest

from lumen.daemon.connectors.gcal import canvas_event_body


class _HttpError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.resp = type("R", (), {"status": status})()


class _Boom:
    def __init__(self, exc):
        self._exc = exc

    def execute(self):
        raise self._exc


class FullFakeEvents(FakeEvents):
    """Adds delete, and lets any verb be told to raise."""

    def __init__(self, sink, raises=None):
        super().__init__(sink)
        self._raises = raises or {}

    def insert(self, calendarId, body, sendUpdates="none"):
        if "insert" in self._raises:
            return _Boom(self._raises["insert"])
        return super().insert(calendarId, body, sendUpdates)

    def patch(self, calendarId, eventId, body, sendUpdates="none"):
        if "patch" in self._raises:
            return _Boom(self._raises["patch"])
        return super().patch(calendarId, eventId, body, sendUpdates)

    def delete(self, calendarId, eventId, sendUpdates="none"):
        self._sink["delete"] = {"cal": calendarId, "id": eventId}
        if "delete" in self._raises:
            return _Boom(self._raises["delete"])
        return _Exec("")


class FullFakeService:
    def __init__(self, sink, raises=None):
        self._sink, self._raises = sink, raises

    def events(self):
        return FullFakeEvents(self._sink, self._raises)


def writer(sink, raises=None):
    return CalendarMarkerWriter(google_cfg=None,
                                service_factory=lambda: FullFakeService(sink, raises))


# --- the pure body builder (no service needed) ---
def test_timed_body_carries_an_explicit_iana_zone():
    """Without it Google interprets the dateTime against the calendar's default
    zone, so the event can silently land at the wrong hour."""
    body = canvas_event_body("CS — HW1 due", "2026-09-01T23:44:59-06:00",
                             "2026-09-01T23:59:59-06:00",
                             tz_name="America/Denver")
    assert body["start"] == {"dateTime": "2026-09-01T23:44:59-06:00",
                             "timeZone": "America/Denver"}
    assert body["end"]["timeZone"] == "America/Denver"


def test_all_day_body_uses_bare_dates():
    body = canvas_event_body("x", "2026-09-01", "2026-09-02", kind="all_day")
    assert body["start"] == {"date": "2026-09-01"}
    assert body["end"] == {"date": "2026-09-02"}
    assert "timeZone" not in body["start"]


def test_body_marks_the_event_as_lumens():
    """The safety net that guarantees we can never delete a user's own event."""
    body = canvas_event_body("x", "a", "b", assignment_id=10, tz_name=None)
    assert body["extendedProperties"]["private"] == {"lumen": "canvas",
                                                     "assignment_id": "10"}


def test_body_reminder_fires_at_the_start():
    body = canvas_event_body("x", "a", "b", tz_name=None)
    assert body["reminders"] == {"useDefault": False,
                                 "overrides": [{"method": "popup", "minutes": 0}]}


def test_body_carries_description_color_and_source():
    body = canvas_event_body("x", "a", "b", description="d", color_id="11",
                             html_url="https://c/a/1", tz_name=None)
    assert body["description"] == "d" and body["colorId"] == "11"
    assert body["source"] == {"title": "Canvas", "url": "https://c/a/1"}


def test_body_omits_empty_optional_fields():
    body = canvas_event_body("x", "a", "b", tz_name=None)
    assert "description" not in body and "colorId" not in body
    assert "source" not in body


# --- the writes ---
def test_create_event_returns_the_id():
    sink = {}
    assert writer(sink).create_event({"summary": "x"}) == "evt_new"
    assert sink["insert"]["cal"] == "primary"


def test_patch_event_reports_success():
    sink = {}
    assert writer(sink).patch_event("evt_1", {"summary": "x"}) is True
    assert sink["patch"]["id"] == "evt_1"


def test_delete_event_reports_success():
    sink = {}
    assert writer(sink).delete_event("evt_1") is True
    assert sink["delete"] == {"cal": "primary", "id": "evt_1"}


@pytest.mark.parametrize("status", [404, 410])
def test_deleting_an_already_absent_event_is_success(status):
    """Treating this as failure wedges the removal queue on a row that can never
    be resolved — the calendar is already in the state we wanted."""
    assert writer({}, {"delete": _HttpError(status)}).delete_event("evt_1") is True


def test_a_real_delete_failure_is_reported():
    assert writer({}, {"delete": _HttpError(500)}).delete_event("evt_1") is False


def test_patch_on_a_hand_deleted_event_fails_so_the_caller_can_recreate():
    assert writer({}, {"patch": _HttpError(404)}).patch_event("e", {}) is False


def test_writes_are_none_or_false_when_not_connected():
    w = CalendarMarkerWriter(google_cfg=None, service_factory=lambda: None)
    assert w.create_event({}) is None
    assert w.patch_event("e", {}) is False
    assert w.delete_event("e") is False


def test_all_day_delegates_still_behave():
    """The original marker API is now a thin delegate; its shape must not move."""
    sink = {}
    assert writer(sink).create_all_day("HW1 due", "2026-09-01") == "evt_new"
    assert sink["insert"]["body"]["end"] == {"date": "2026-09-02"}
    assert writer(sink).patch_all_day("evt_1", "2026-09-05") is True
