"""SwipeGesture (pure distance/threshold core) and SwipeNavigator (the
commit-on-release event filter) behind the app-wide back/forward swipe (#18).

Browser feel: the indicator tracks the swipe live, but navigation fires only
when the gesture ends past the threshold — right = back, left = forward.
"""
from lumen.ui_v3.nav_history import NavController, NavEntry
from lumen.ui_v3.swipe_nav import BACK, FORWARD, SwipeGesture, SwipeNavigator


# ---- SwipeGesture: pure, no Qt --------------------------------------------

def test_swipe_right_is_back_and_progress_ramps():
    g = SwipeGesture(threshold=100)
    g.begin()
    g.update(60)
    assert g.side == BACK
    assert g.progress == 0.6
    assert not g.passed
    g.update(60)                 # 120 total, clamped
    assert g.progress == 1.0
    assert g.passed


def test_swipe_left_is_forward():
    g = SwipeGesture(threshold=100)
    g.begin()
    g.update(-130)
    assert g.side == FORWARD
    assert g.passed


def test_below_threshold_does_not_pass():
    g = SwipeGesture(threshold=100)
    g.begin()
    g.update(40)
    assert not g.passed
    assert g.side == BACK


def test_reset_clears_accumulation():
    g = SwipeGesture(threshold=100)
    g.begin()
    g.update(200)
    g.reset()
    assert g.side is None
    assert g.progress == 0.0
    assert not g.passed


# ---- SwipeNavigator: gesture -> controller + indicator --------------------

class FakeIndicator:
    def __init__(self):
        self.progress_calls = []
        self.dismissed = 0

    def set_progress(self, side, p, capped):
        self.progress_calls.append((side, p, capped))

    def dismiss(self):
        self.dismissed += 1


def _navctl():
    nav = NavController(NavEntry("today"))
    nav.visit(NavEntry("mail"))
    nav.visit(NavEntry("calendar"))       # can_back, cannot_forward
    return nav


def _navigator(qtbot, nav):
    restored = []
    ind = FakeIndicator()
    swipe = SwipeNavigator(nav, restored.append, ind, threshold=100)
    return swipe, restored, ind


def test_full_right_swipe_release_navigates_back(qtbot):
    nav = _navctl()
    swipe, restored, ind = _navigator(qtbot, nav)
    # begin carries no movement and is deliberately not consumed (see
    # test_phase_events_are_never_swallowed) — only the update below is ours.
    swipe._handle_wheel(0, 0, "begin")
    swipe._handle_wheel(120, 0, "update")
    assert ind.progress_calls[-1] == (BACK, 1.0, False)
    swipe._handle_wheel(0, 0, "end")
    assert restored == [NavEntry("mail")]
    assert ind.dismissed == 1


def test_short_swipe_release_does_not_navigate(qtbot):
    nav = _navctl()
    swipe, restored, ind = _navigator(qtbot, nav)
    swipe._handle_wheel(0, 0, "begin")
    swipe._handle_wheel(40, 0, "update")
    swipe._handle_wheel(0, 0, "end")
    assert restored == []
    assert ind.dismissed == 1


def test_left_swipe_release_navigates_forward(qtbot):
    nav = _navctl()
    nav.back()                            # now mid-history: forward available
    swipe, restored, ind = _navigator(qtbot, nav)
    swipe._handle_wheel(0, 0, "begin")
    swipe._handle_wheel(-120, 0, "update")
    swipe._handle_wheel(0, 0, "end")
    assert restored == [NavEntry("calendar")]


def test_vertical_scroll_is_ignored(qtbot):
    nav = _navctl()
    swipe, restored, ind = _navigator(qtbot, nav)
    assert not swipe._handle_wheel(5, 200, "update")   # dy dominates
    assert restored == []
    assert ind.progress_calls == []


def test_at_history_end_swipe_is_capped_and_bounces(qtbot):
    nav = _navctl()                       # at "calendar": cannot go forward
    swipe, restored, ind = _navigator(qtbot, nav)
    swipe._handle_wheel(0, 0, "begin")
    swipe._handle_wheel(-120, 0, "update")           # try forward past threshold
    assert ind.progress_calls[-1] == (FORWARD, 1.0, True)   # capped/bounce
    swipe._handle_wheel(0, 0, "end")
    assert restored == []                 # never commits at the edge


def test_phase_platform_waits_for_release_not_idle_timer(qtbot):
    # #31: on a platform that delivers scroll phases, reaching the threshold and
    # then HOLDING (fingers still down, no lift) must NOT auto-commit. The idle
    # timer is only a NoScrollPhase fallback — with real phases we wait for the
    # "end" (finger lift), so a held-at-the-edge swipe stays put until release.
    nav = _navctl()
    swipe, restored, ind = _navigator(qtbot, nav)
    swipe._handle_wheel(0, 0, "begin")
    swipe._handle_wheel(120, 0, "update")
    assert not swipe._idle.isActive()        # not armed — waiting for the lift
    assert restored == []                    # nothing committed while held
    swipe._handle_wheel(0, 0, "end")         # finger lift
    assert restored == [NavEntry("mail")]


def test_no_phase_fallback_commits_on_idle_timeout(qtbot):
    # Trackpads that report NoScrollPhase never send "end"; the inactivity
    # timer stands in for release.
    nav = _navctl()
    swipe, restored, ind = _navigator(qtbot, nav)
    swipe._handle_wheel(120, 0, None)
    assert swipe._idle.isActive()
    swipe._idle_timeout()                 # simulate the timer firing
    assert restored == [NavEntry("mail")]


# ---- the filter must not swallow other widgets' scrolling ------------------

def test_phase_events_are_never_swallowed(qtbot):
    # A wheel gesture's ScrollBegin/ScrollEnd carry the phase, not movement.
    # Consuming them broke scrolling inside the Canvas tab's QWebEngineView:
    # Chromium builds its scroll from the phase stream, so a swallowed
    # ScrollBegin means the following updates scroll nothing (live bug,
    # Wayland delivers phases). The navigator tracks the phase but must let
    # the event through — it only owns the horizontal-dominant updates.
    nav = _navctl()
    swipe, restored, ind = _navigator(qtbot, nav)
    assert not swipe._handle_wheel(0, 0, "begin")
    assert not swipe._handle_wheel(0, 300, "update")     # vertical: not ours
    assert not swipe._handle_wheel(0, 0, "end")
    assert restored == []


def test_horizontal_updates_are_still_consumed(qtbot):
    nav = _navctl()
    swipe, restored, ind = _navigator(qtbot, nav)
    swipe._handle_wheel(0, 0, "begin")
    assert swipe._handle_wheel(120, 0, "update")         # ours: consume it
    swipe._handle_wheel(0, 0, "end")
    assert restored == [NavEntry("mail")]


def test_marked_subtree_is_out_of_scope(qtbot):
    # The embedded browser marks itself lumen_swipe_ignore: inside a web view a
    # two-finger swipe belongs to the page, not to app navigation.
    from PyQt6.QtWidgets import QWidget
    root = QWidget()
    qtbot.addWidget(root)
    webish = QWidget(root)
    webish.setProperty("lumen_swipe_ignore", True)
    child = QWidget(webish)
    plain = QWidget(root)
    swipe = SwipeNavigator(_navctl(), lambda e: None, FakeIndicator(),
                           threshold=100, within=root)
    assert swipe._in_scope(plain)
    assert not swipe._in_scope(webish)
    assert not swipe._in_scope(child)
