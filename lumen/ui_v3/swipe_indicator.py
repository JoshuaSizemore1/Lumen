"""SwipeIndicator — the small circle+arrow overlay that drags in from a screen
edge during a back/forward swipe (#18).

Browser affordance: swipe right → a circle slides in from the LEFT edge with a
'‹' (left) arrow (back); swipe left → in from the RIGHT edge with a '›' (right)
arrow (forward). The arrow points opposite the swipe, as requested. Position
and opacity track the gesture's 0→1 progress; a 'capped' state (no history that
way) renders dimmed and never travels the full inset — the "can't go" bounce.

Mouse-transparent so it never eats clicks. Painting only; all decision logic
lives in SwipeNavigator.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from . import theme as T
from .swipe_nav import BACK

DIAM = 40          # circle diameter (unscaled)
INSET = 26         # how far in from the edge the circle rests at full progress
MARGIN = 16        # painting slack around the circle for the soft shadow


class SwipeIndicator(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setFixedSize(T.sc(DIAM + 2 * MARGIN), T.sc(DIAM + 2 * MARGIN))
        self._side = BACK
        self._progress = 0.0
        self._capped = False
        self.hide()

    # ---- driven by SwipeNavigator ----------------------------------------
    def set_progress(self, side: str, p: float, capped: bool) -> None:
        self._side = side
        self._progress = max(0.0, min(1.0, p))
        self._capped = capped
        self._reposition()
        if not self.isVisible():
            self.show()
        self.raise_()
        self.update()

    def dismiss(self) -> None:
        self._progress = 0.0
        self.hide()

    # ---- geometry + paint -------------------------------------------------
    def _reposition(self) -> None:
        par = self.parentWidget()
        if par is None:
            return
        # A capped gesture only pulls partway in (rubber-band).
        reach = self._progress * (0.55 if self._capped else 1.0)
        inset = T.sc(INSET)
        w = self.width()
        travel = inset + w                       # fully offscreen → resting inset
        if self._side == BACK:
            x = int(-w + reach * travel)         # slides in from the left edge
        else:
            x = int(par.width() - reach * travel)  # in from the right edge
        y = (par.height() - self.height()) // 2
        self.move(x, y)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        m = T.sc(MARGIN)
        d = T.sc(DIAM)
        alpha = int(70 + 150 * self._progress)
        if self._capped:
            alpha = int(alpha * 0.5)
        # circle
        p.setPen(QPen(QColor(0, 0, 0, 22), 1))
        p.setBrush(QColor(T.BG_MAIN))
        p.drawEllipse(m, m, d, d)
        # arrow glyph — '‹' for back, '›' for forward (opposite the swipe)
        col = QColor(T.ACCENT)
        col.setAlpha(alpha if self._progress else 90)
        p.setPen(col)
        f = QFont()
        f.setPixelSize(int(d * 0.72))
        f.setBold(True)
        p.setFont(f)
        glyph = "‹" if self._side == BACK else "›"
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, glyph)
        p.end()
