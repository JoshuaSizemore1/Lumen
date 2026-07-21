"""App-wide back/forward swipe gesture (#18) — browser style.

Two-finger horizontal scroll drives a circle+arrow indicator that tracks the
swipe live; navigation commits only when the gesture *ends* past a threshold
(fingers lifted), exactly like Safari/Chrome overscroll. Right = back,
left = forward; the arrow points opposite the swipe.

Three pieces, kept apart so the logic is testable without a live trackpad:
- `SwipeGesture` — pure distance/threshold accumulator (no Qt).
- `SwipeNavigator` — a wheel-event filter that maps scroll phases to the
  gesture, drives the indicator, and commits against a `NavController`.
- `SwipeIndicator` (swipe_indicator.py) — the painted overlay.

Release detection uses Qt scroll *phases* when the platform delivers them, and
falls back to an inactivity timer for touchpads that report NoScrollPhase
(seen on some X11/Wayland setups).
"""
from PyQt6.QtCore import QEvent, QObject, QTimer
from PyQt6.QtWidgets import QWidget

BACK = "back"
FORWARD = "forward"


class SwipeGesture:
    """Accumulates horizontal swipe distance for one gesture. Right (positive)
    is back, left (negative) is forward."""

    def __init__(self, threshold: float = 100.0):
        self._threshold = float(threshold)
        self._accum = 0.0

    def begin(self) -> None:
        self._accum = 0.0

    def update(self, dx: float) -> None:
        self._accum += dx

    def reset(self) -> None:
        self._accum = 0.0

    @property
    def side(self) -> str | None:
        if self._accum > 0:
            return BACK
        if self._accum < 0:
            return FORWARD
        return None

    @property
    def progress(self) -> float:
        return min(abs(self._accum) / self._threshold, 1.0)

    @property
    def passed(self) -> bool:
        return abs(self._accum) >= self._threshold


def _map_phase(phase) -> str | None:
    """Qt scroll phase -> our coarse states. Momentum (post-lift inertia) is
    treated as release so we commit the instant the fingers come up."""
    from PyQt6.QtCore import Qt
    P = Qt.ScrollPhase
    return {
        P.ScrollBegin: "begin",
        P.ScrollUpdate: "update",
        P.ScrollEnd: "end",
        P.ScrollMomentum: "end",
    }.get(phase)                       # NoScrollPhase -> None (timer fallback)


class SwipeNavigator(QObject):
    """Installs on a target widget as a wheel-event filter and turns horizontal
    two-finger swipes into NavController back/forward, with the indicator
    tracking the gesture and committing only on release."""

    IDLE_MS = 120

    def __init__(self, nav, restore, indicator, *, threshold: float = 100.0,
                 within=None, parent=None):
        super().__init__(parent)
        self._nav = nav
        self._restore = restore
        self._indicator = indicator
        # When installed on the QApplication, only act on wheel events inside
        # this content root (so the whole app is covered even where a child
        # scroll area would otherwise swallow the event first).
        self._within = within
        self._gesture = SwipeGesture(threshold)
        self._active = False
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.setInterval(self.IDLE_MS)
        self._idle.timeout.connect(self._idle_timeout)

    # ---- Qt plumbing ------------------------------------------------------
    def _in_scope(self, obj) -> bool:
        if self._within is None:
            return True
        if not isinstance(obj, QWidget):
            return False
        return obj is self._within or self._within.isAncestorOf(obj)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel and self._in_scope(obj):
            px = event.pixelDelta()
            ang = event.angleDelta()
            dx = px.x() if px.x() else ang.x()
            dy = px.y() if px.y() else ang.y()
            if self._handle_wheel(dx, dy, _map_phase(event.phase())):
                event.accept()
                return True
        return super().eventFilter(obj, event)

    # ---- gesture logic (unit-tested directly) -----------------------------
    def _handle_wheel(self, dx: float, dy: float, phase: str | None) -> bool:
        # Phase transitions carry no movement (dx/dy are often zero), so they
        # bypass the horizontal guard — otherwise a zero-delta ScrollEnd would
        # look "vertical" and never commit.
        if phase == "end":
            if self._active:
                self._finish()
                return True
            return False
        if phase == "begin":
            self._begin()
            return True
        # Movement (ScrollUpdate or the NoScrollPhase fallback): only act on a
        # horizontal-dominant delta so vertical scrolling is left untouched.
        if abs(dx) <= abs(dy):
            return False
        if not self._active:
            self._begin()
        self._gesture.update(dx)
        self._sync_indicator()
        self._idle.start()                 # release fallback when phases are absent
        return True

    def _begin(self) -> None:
        self._active = True
        self._gesture.begin()

    def _can(self, side: str | None) -> bool:
        if side == BACK:
            return self._nav.can_back()
        if side == FORWARD:
            return self._nav.can_forward()
        return False

    def _sync_indicator(self) -> None:
        side = self._gesture.side
        if side is None:
            return
        self._indicator.set_progress(side, self._gesture.progress,
                                     not self._can(side))

    def _idle_timeout(self) -> None:
        self._finish()

    def _finish(self) -> None:
        self._idle.stop()
        if not self._active:
            return
        self._active = False
        side = self._gesture.side
        passed = self._gesture.passed
        self._gesture.reset()
        self._indicator.dismiss()
        if passed and self._can(side):
            self._commit(side)

    def _commit(self, side: str) -> None:
        entry = self._nav.back() if side == BACK else self._nav.forward()
        if entry is not None:
            self._restore(entry)
