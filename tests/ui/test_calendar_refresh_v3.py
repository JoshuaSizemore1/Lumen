"""#59: the calendar has to be refreshable without restarting Lumen.

Josh's report was that new Canvas assignments only appeared on the calendar once
he closed Lumen entirely. The daemon half of that is fixed in CanvasSync (the
Canvas pass now re-reads the window it just wrote). This is the other half: the
UI could never ask for a refresh at all, because `calendar.list` is a pure cache
read. Same shape as Mail's ↻ / debounced tab-entry sync, which already works.
"""
from datetime import date

from PyQt6.QtCore import QObject, pyqtSignal

from lumen.ui_v3.screens.calendar import CalendarScreen
from lumen.ui_v3.state import AppState


class FakeClient(QObject):
    chunk = pyqtSignal(str)
    done = pyqtSignal()
    error = pyqtSignal(str)
    tool_used = pyqtSignal(str)
    model_off = pyqtSignal()
    conversation = pyqtSignal(int)
    captured = pyqtSignal(dict)
    confirm_requested = pyqtSignal(dict)
    compose_requested = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.requests: list[tuple] = []
        self.sent: list[tuple] = []

    def request(self, type_, payload, cb):
        self.requests.append((type_, payload, cb))

    def send(self, type_, payload):
        self.sent.append((type_, payload))

    def types(self):
        return [t for t, _p, _cb in self.requests]


def _state():
    data = FakeClient()
    st = AppState(data=data)
    data.requests.clear()
    return data, st


# ---- state: manual refresh vs debounced auto-sync ---------------------------
def test_refresh_calendar_asks_the_daemon_to_sync(qtbot):
    data, st = _state()
    st.refresh_calendar("2026-08-01", "2026-08-31", lambda _r: None)
    assert data.requests[0][0] == "calendar.refresh"
    assert data.requests[0][1] == {"from": "2026-08-01", "to": "2026-08-31"}


def test_sync_calendar_debounces_to_a_plain_cache_read(qtbot):
    """Flicking between tabs must not turn into a Google round trip each time —
    the power budget on this laptop is the hard constraint. Inside the window
    the cache is still re-read, so the view is never stale-by-omission."""
    data, st = _state()
    st.refresh_calendar("2026-08-01", "2026-08-31", lambda _r: None)
    data.requests.clear()
    st.sync_calendar("2026-08-01", "2026-08-31", lambda _r: None)
    assert data.types() == ["calendar.list"]

    st._last_cal_sync_req -= AppState.SYNC_DEBOUNCE_S
    data.requests.clear()
    st.sync_calendar("2026-08-01", "2026-08-31", lambda _r: None)
    assert data.types() == ["calendar.refresh"]


def test_first_sync_calendar_of_a_session_really_syncs(qtbot):
    data, st = _state()
    st.sync_calendar("2026-08-01", "2026-08-31", lambda _r: None)
    assert data.types() == ["calendar.refresh"]


def test_both_paths_normalize_events_the_same_way(qtbot):
    """refresh and list answer in the same shape, so the callback cannot care
    which one it got."""
    data, st = _state()
    seen = []
    st.refresh_calendar("2026-08-01", "2026-08-31", seen.append)
    data.requests[0][2]({"events": [{"id": "e1", "calendar_id": "primary",
                                     "title": "HW1", "start_at": "2026-08-04",
                                     "end_at": "2026-08-05", "all_day": True}],
                         "connected": True, "window": ["2026-07-01", "2026-10-01"]})
    assert seen[0]["events"][0]["title"] == "HW1"
    assert seen[0]["connected"] is True


# ---- screen: tab entry syncs, the ↻ button forces --------------------------
def _screen(qtbot):
    data, st = _state()
    scr = CalendarScreen(st)
    qtbot.addWidget(scr)
    return data, scr


def test_entering_the_tab_syncs_rather_than_re_reading_the_cache(qtbot):
    """The screen already refetched on show — but only from the daemon's cache,
    which is exactly why clicking around never surfaced the new assignments."""
    data, scr = _screen(qtbot)
    data.requests.clear()
    scr.showEvent(None)
    assert data.types() == ["calendar.refresh"]


def test_the_refresh_button_always_syncs_even_inside_the_debounce(qtbot):
    """Pressing ↻ has to mean something every time. A button that silently did
    nothing would be worse than no button."""
    data, scr = _screen(qtbot)
    scr.showEvent(None)                      # opens the debounce window
    data.requests.clear()
    scr.force_refresh()
    assert data.types() == ["calendar.refresh"]


def test_moving_between_months_stays_on_the_cache(qtbot):
    """Month arrows are navigation, not a sync request — one Google fetch per
    chevron press is exactly the tight polling the project forbids."""
    data, scr = _screen(qtbot)
    scr.showEvent(None)
    data.requests.clear()
    scr._shift(1)
    assert data.types() == ["calendar.list"]


def test_the_refresh_button_is_disabled_while_a_sync_is_in_flight(qtbot):
    """A calendar sync is a real network round trip; without this the button
    invites a queue of them."""
    data, scr = _screen(qtbot)
    data.requests.clear()
    scr.force_refresh()
    assert scr.refresh_btn.isEnabled() is False
    data.requests[0][2]({"events": [], "connected": True, "window": None})
    assert scr.refresh_btn.isEnabled() is True


def test_a_dead_daemon_reply_still_re_enables_the_button(qtbot):
    """An error reply is still a reply — the button must not latch off."""
    data, scr = _screen(qtbot)
    data.requests.clear()
    scr.force_refresh()
    data.requests[0][2]({"error": "calendar unavailable"})
    assert scr.refresh_btn.isEnabled() is True


def test_the_screen_asks_for_its_own_visible_range(qtbot):
    data, scr = _screen(qtbot)
    scr.view = "day"
    scr.anchor = date(2026, 8, 4)
    data.requests.clear()
    scr.force_refresh()
    assert data.requests[0][1] == {"from": "2026-08-04", "to": "2026-08-04"}


# ---- the header had no room left to give -----------------------------------
def test_the_legend_yields_when_the_header_runs_out_of_room(qtbot):
    """Adding ↻ tipped an already-tight header over: in a 1280px window Qt
    clipped the view segments mid-word, rendering "Month" as "Iont". The legend
    is the only decorative item in the row, so it is the one that goes.

    Widths here are the *screen's*, which is the window minus the sidebar — a
    1280px window leaves this widget about 1005."""
    _data, scr = _screen(qtbot)
    scr.show()
    scr.resize(1645, 800)                 # a 1920 window
    assert scr.legend.isVisibleTo(scr) is True
    scr.resize(1005, 800)                 # a 1280 window
    assert scr.legend.isVisibleTo(scr) is False
    scr.resize(1645, 800)                 # and it comes back
    assert scr.legend.isVisibleTo(scr) is True
