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
