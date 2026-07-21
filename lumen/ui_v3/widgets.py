"""Shared building blocks for the Relay-styled UI.

Note the font default is inverted from ui_v2: Relay is a *sans-first* design —
body copy, headings, and controls are Hanken Grotesk (IBM Plex Sans here), and
mono is reserved for eyebrows, counts, times, and keyboard hints. So
`font(px)` returns sans; pass `mono=True` for the accent face.
"""
import math

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPalette, QPen, QPolygonF
from PyQt6.QtWidgets import (
    QAbstractButton, QComboBox, QFrame, QGraphicsDropShadowEffect, QHBoxLayout,
    QLabel, QLayout, QPushButton, QScrollArea, QSizePolicy, QTextBrowser,
    QVBoxLayout, QWidget,
)

from . import theme as T


# ---------------------------------------------------------------- helpers
def qcolor(spec: str, alpha: int | None = None) -> QColor:
    """CSS-style hex (#rrggbb or #rrggbbaa, alpha LAST) -> QColor."""
    if isinstance(spec, str) and len(spec) == 9 and spec.startswith("#"):
        c = QColor(spec[:7])
        c.setAlpha(int(spec[7:9], 16))
        return c
    c = QColor(spec)
    if alpha is not None:
        c.setAlpha(alpha)
    return c


def font(px: float, weight: int = 400, mono: bool = False,
         ls: float = 0.0) -> QFont:
    f = QFont(T.FONT_MONO if mono else T.FONT_SANS)
    px = px * T.FONT_SCALE          # the Settings text-size percentage
    # The mockup uses fractional sizes (12.5px, 9.5px); QFont wants ints for
    # pixel size, so carry the fraction through point-size instead.
    if float(px).is_integer():
        f.setPixelSize(int(px))
    else:
        f.setPointSizeF(float(px) * 0.75)
    f.setWeight(QFont.Weight(weight))
    if ls:
        # Tracking is a typographic ratio, so it scales with the type.
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, ls * T.FONT_SCALE)
    return f


def label(text: str, px: float, color: str, weight: int = 400,
          mono: bool = False, ls: float = 0.0, wrap: bool = False,
          strike: bool = False) -> QLabel:
    w = QLabel(text)
    f = font(px, weight, mono, ls)
    if strike:
        f.setStrikeOut(True)
    w.setFont(f)
    pal = w.palette()
    pal.setColor(QPalette.ColorRole.WindowText, qcolor(color))
    w.setPalette(pal)
    if wrap:
        w.setWordWrap(True)
    return w


def eyebrow(text: str, color: str = T.TEXT_FAINT, px: float = 10,
            ls: float = 1.5) -> QLabel:
    """The mockup's recurring section marker: mono, uppercase, wide tracking."""
    return label(text.upper(), px, color, mono=True, ls=ls)


# Every layout in the app is built through these two, which is why the font
# scale is applied here rather than at ~300 call sites: padding and gaps are
# set in the design's own px, and at 50% unscaled margins ate the sidebar
# (clipping "Calendar" to "Calend"), while at 150% they left it cramped.
def hbox(host: QWidget | None = None, m=(0, 0, 0, 0), s: int = 0) -> QHBoxLayout:
    lay = QHBoxLayout(host) if host else QHBoxLayout()
    lay.setContentsMargins(*(T.sc(v) if v else 0 for v in m))
    lay.setSpacing(T.sc(s) if s else 0)
    return lay


def vbox(host: QWidget | None = None, m=(0, 0, 0, 0), s: int = 0) -> QVBoxLayout:
    lay = QVBoxLayout(host) if host else QVBoxLayout()
    lay.setContentsMargins(*(T.sc(v) if v else 0 for v in m))
    lay.setSpacing(T.sc(s) if s else 0)
    return lay


def clear_layout(lay) -> None:
    while lay.count():
        item = lay.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)   # detach now, not on the next event-loop tick
            w.deleteLater()
        elif item.layout():
            clear_layout(item.layout())


def hline(color: str = T.BORDER_FAINT) -> QFrame:
    f = QFrame()
    f.setFixedHeight(1)
    pal = f.palette()
    pal.setColor(QPalette.ColorRole.Window, qcolor(color))
    f.setPalette(pal)
    f.setAutoFillBackground(True)
    return f


def vline(color: str = T.BORDER_MED) -> QFrame:
    f = QFrame()
    f.setFixedWidth(1)
    pal = f.palette()
    pal.setColor(QPalette.ColorRole.Window, qcolor(color))
    f.setPalette(pal)
    f.setAutoFillBackground(True)
    return f


def scroll(inner: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setWidget(inner)
    sa.setFrameShape(QFrame.Shape.NoFrame)
    sa.viewport().setAutoFillBackground(False)
    inner.setAutoFillBackground(False)
    return sa


def scroll_fixed(inner: QWidget, width: int) -> QScrollArea:
    """Scrolling side panel of a fixed width.

    The width has to go on the *scroll area*, not the inner widget: fixing the
    inner one leaves the viewport free to be narrower, which produces a
    horizontal scrollbar and content running past the window edge.
    """
    sa = scroll(inner)
    sa.setFixedWidth(width)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    return sa


class Select(QComboBox):
    """QComboBox that paints its own chevron.

    QSS cannot draw one — `::down-arrow` has no glyph primitive and the CSS
    zero-size-plus-borders triangle trick renders as a filled block — while the
    native arrow clashes with the cream palette.
    """

    def __init__(self, on_panel: bool = False):
        super().__init__()
        if on_panel:
            self.setProperty("cls", "onpanel")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, ev):
        super().paintEvent(ev)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(T.TEXT_FAINT))
        k = T.FONT_SCALE
        x, y = self.width() - 16 * k, self.height() / 2 - 1
        p.drawPolygon(QPolygonF([QPointF(x, y), QPointF(x + 8 * k, y),
                                 QPointF(x + 4 * k, y + 4.5 * k)]))


def paint_glyph(p: QPainter, kind: str, box: QRectF, color: str,
                stroke: float = 1.6) -> None:
    """Stroke a small UI icon centred in `box`.

    These were typographic characters — ‹ › for the calendar's month arrows, ⟳
    for refresh, ⌕ for search. All three are punctuation being asked to act as
    icons: they inherit the font's own optical size and sidebearings, so in a
    28px control they render as a comma adrift in a large empty box, and they
    drift again at a different text scale. Stroking them ties the icon to the
    control instead of to the font — the same reason TodoCheck draws its tick.
    """
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(qcolor(color), stroke)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    cx, cy = box.center().x(), box.center().y()
    r = min(box.width(), box.height()) / 2

    if kind in ("chevron-left", "chevron-right"):
        w, h = r * 0.34, r * 0.50
        tip = cx + (w / 2 if kind == "chevron-right" else -w / 2)
        back = cx - (w / 2 if kind == "chevron-right" else -w / 2)
        p.drawPolyline(QPolygonF([QPointF(back, cy - h), QPointF(tip, cy),
                                  QPointF(back, cy + h)]))
    elif kind == "refresh":
        rr = r * 0.58
        end = math.radians(340)                    # the ring is open on the right
        p.drawArc(QRectF(cx - rr, cy - rr, rr * 2, rr * 2),
                  int(50 * 16), int(290 * 16))
        # A solid head, not two more strokes: at 28px an outlined arrowhead
        # blurs into the ring and the whole mark just reads as the letter C.
        px_, py = cx + rr * math.cos(end), cy - rr * math.sin(end)
        dx, dy = -math.sin(end), -math.cos(end)    # tangent, in screen coords
        nx, ny = -dy, dx
        p.setBrush(qcolor(color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(QPolygonF([
            QPointF(px_ + dx * rr * 0.62, py + dy * rr * 0.62),
            QPointF(px_ + nx * rr * 0.40, py + ny * rr * 0.40),
            QPointF(px_ - nx * rr * 0.40, py - ny * rr * 0.40)]))
    elif kind == "search":
        rr = r * 0.52
        c = QPointF(cx - r * 0.12, cy - r * 0.12)
        p.drawEllipse(c, rr, rr)
        off = rr * 0.72
        p.drawLine(QPointF(c.x() + off, c.y() + off),
                   QPointF(cx + r * 0.72, cy + r * 0.72))


class Glyph(QWidget):
    """Non-interactive stroked icon (the search mark in the mail filter)."""

    def __init__(self, kind: str, size: int, color: str = T.TEXT_FAINT,
                 stroke: float = 1.5):
        super().__init__()
        self._kind, self._color, self._stroke = kind, color, stroke
        self.setFixedSize(T.sc(size), T.sc(size))

    def paintEvent(self, ev):
        p = QPainter(self)
        paint_glyph(p, self._kind, QRectF(self.rect()), self._color,
                    self._stroke * T.FONT_SCALE)


class IconButton(QPushButton):
    """Square icon button (calendar month arrows, mail refresh).

    Sized as a square so it sits level with the text buttons beside it, and
    painted rather than lettered — see `paint_glyph`.
    """

    def __init__(self, kind: str, size: int = 28, on_click=None,
                 tooltip: str = "", color: str | None = None):
        super().__init__()
        self._kind = kind
        self._color = color or T.TEXT_SECONDARY
        self.setProperty("cls", "icon")
        self.setFixedSize(T.sc(size), T.sc(size))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)
        if on_click:
            self.clicked.connect(on_click)

    def paintEvent(self, ev):
        super().paintEvent(ev)      # QSS paints the border and hover fill
        p = QPainter(self)
        paint_glyph(p, self._kind, QRectF(self.rect()), self._color,
                    1.6 * T.FONT_SCALE)


def shadow(w: QWidget, blur: int, dy: int, alpha: int) -> None:
    """QSS has no box-shadow; the mockup's dialogs lean on one heavily."""
    eff = QGraphicsDropShadowEffect(w)
    eff.setBlurRadius(blur)
    eff.setOffset(0, dy)
    eff.setColor(QColor(60, 45, 25, alpha))
    w.setGraphicsEffect(eff)


def button(text: str, variant: str, px: float | None = None,
           height: int | None = None) -> QPushButton:
    # Escape "&": Qt reads it as a mnemonic marker and would render
    # "Review & send" as "Review _send". The mock has no mnemonics anywhere.
    b = QPushButton(text.replace("&", "&&"))
    b.setProperty("variant", variant)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if px is not None:
        b.setFont(font(px, 600 if variant == "primary" else 400))
    if height is not None:
        b.setFixedHeight(T.sc(height))     # a control sized to hold its text
    return b


def seg_button(text: str) -> QPushButton:
    """Segmented control (Month/Week/Day, Due date/Tag) — mono, checkable."""
    b = QPushButton(text)
    b.setProperty("cls", "seg")
    b.setCheckable(True)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFont(font(11, mono=True))
    b.setFixedHeight(T.sc(28))    # level with the buttons either side of it
    return b


def empty_state(text: str, sub: str | None = None) -> QWidget:
    w = QWidget()
    lay = vbox(w, (0, 0, 0, 0), 5)
    lay.addStretch(1)
    title = label(text, 13, T.TEXT_FAINT)
    title.setAlignment(Qt.AlignmentFlag.AlignCenter)
    title.setWordWrap(True)
    lay.addWidget(title)
    if sub:
        s = label(sub, 11, T.TEXT_FAINTER)
        s.setAlignment(Qt.AlignmentFlag.AlignCenter)
        s.setWordWrap(True)
        lay.addWidget(s)
    lay.addStretch(1)
    return w


# ---------------------------------------------------------------- widgets
class ElideLabel(QLabel):
    """Single-line label that elides with '…' instead of forcing width open.
    The mockup uses text-overflow:ellipsis on nearly every list row."""

    def __init__(self, text: str, px: float, color: str, weight: int = 400,
                 mono: bool = False, align=Qt.AlignmentFlag.AlignLeft):
        super().__init__(text)
        self.setFont(font(px, weight, mono))
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.WindowText, qcolor(color))
        self.setPalette(pal)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(24)
        self._align = align

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setFont(self.font())
        p.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        # contentsRect, not rect: month-view chips carry a left color rule and
        # padding, and drawing at the widget origin would run text under it.
        r = self.contentsRect()
        t = self.fontMetrics().elidedText(self.text(),
                                          Qt.TextElideMode.ElideRight, r.width())
        p.drawText(r, self._align | Qt.AlignmentFlag.AlignVCenter, t)


class TypingDots(QLabel):
    """Prefix with cycling trailing dots — the 'thinking' state. The timer runs
    only while animating and stops on set_static()/hide, so no repaint loop
    survives the answer (power/thermal discipline)."""

    def __init__(self, prefix: str, px: float, color: str, weight: int = 400,
                 mono: bool = True, ls: float = 0.0, period_ms: int = 400,
                 max_dots: int = 3):
        super().__init__()
        self._prefix, self._n, self._max = prefix, 1, max_dots
        self.setFont(font(px, weight, mono, ls))
        self._recolor(color)
        self._timer = QTimer(self)
        self._timer.setInterval(period_ms)
        self._timer.timeout.connect(self._tick)

    def _recolor(self, color: str):
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.WindowText, qcolor(color))
        self.setPalette(pal)

    def start(self, prefix: str | None = None):
        if prefix is not None:
            self._prefix = prefix
        self._n = 1
        self.setText(f"{self._prefix}.")
        self._timer.start()

    def _tick(self):
        self._n = self._n % self._max + 1
        self.setText(self._prefix + "." * self._n)

    def stop(self):
        self._timer.stop()

    def set_static(self, text: str, color: str | None = None):
        self._timer.stop()
        if color is not None:
            self._recolor(color)
        self.setText(text)

    def hideEvent(self, ev):
        self._timer.stop()
        super().hideEvent(ev)


class Dot(QWidget):
    """Fixed-size dot / rounded square: status dots, legend swatches, unread."""

    def __init__(self, size: int, color: str, radius: float | None = None,
                 border: str | None = None):
        super().__init__()
        self.setFixedSize(size, size)
        self._color = color
        self._radius = size / 2 if radius is None else radius
        self._border = border

    def set_color(self, color: str, border: str | None = None):
        self._color, self._border = color, border
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self._color and self._color != "transparent":
            p.setBrush(qcolor(self._color))
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(qcolor(self._border), 1) if self._border
                 else Qt.PenStyle.NoPen)
        p.drawRoundedRect(r, self._radius, self._radius)


class Chip(QLabel):
    """Bordered pill/chip (tags, kbd hints, badges, label pills)."""

    def __init__(self, text: str, fg: str, border: str | None,
                 bg: str | None = None, px: float = 10, radius: int = 3,
                 hpad: int = 8, vpad: int = 3, weight: int = 400,
                 mono: bool = True, ls: float = 0.0):
        super().__init__(text)
        self._fg, self._border, self._bg = fg, border, bg
        self._radius, self._hpad, self._vpad = radius, hpad, vpad
        self._ls = ls
        self.setFont(font(px, weight, mono, ls))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def restyle(self, fg: str, border: str | None, bg: str | None = None):
        self._fg, self._border, self._bg = fg, border, bg
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        # Padding is a text metric, so it tracks the font scale; the +2 is the
        # 1px border on each side, which does not.
        fm = self.fontMetrics()
        return QSize(fm.horizontalAdvance(self.text()) + 2 * T.sc(self._hpad) + 2,
                     fm.height() + 2 * T.sc(self._vpad) + 2)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def _text_rect(self) -> QRect:
        """Letter-spacing is applied after the last glyph too, so a centred
        string sits half a space left of centre. Give the rect that back."""
        return self.rect().adjusted(round(self._ls * T.FONT_SCALE), 0, 0, 0)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(qcolor(self._bg) if self._bg else Qt.BrushStyle.NoBrush)
        p.setPen(QPen(qcolor(self._border), 1) if self._border
                 else Qt.PenStyle.NoPen)
        p.drawRoundedRect(r, self._radius, self._radius)
        p.setPen(qcolor(self._fg))
        p.setFont(self.font())
        p.drawText(self._text_rect(), Qt.AlignmentFlag.AlignCenter, self.text())


class ClickChip(Chip):
    """Chip that fires a callback (mail scope filters, label suggestions)."""

    def __init__(self, text: str, fg: str, border: str | None,
                 bg: str | None = None, px: float = 9, radius: int = 3,
                 hpad: int = 7, vpad: int = 2, on_click=None,
                 tooltip: str = "", ls: float = 0.5, dashed: bool = False):
        super().__init__(text, fg, border, bg, px=px, radius=radius,
                         hpad=hpad, vpad=vpad, ls=ls)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)
        self._on_click = on_click
        self._dashed = dashed

    def paintEvent(self, ev):
        if not self._dashed:
            return super().paintEvent(ev)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        pen = QPen(qcolor(self._border), 1)
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(qcolor(self._bg) if self._bg else Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, self._radius, self._radius)
        p.setPen(qcolor(self._fg))
        p.setFont(self.font())
        p.drawText(self._text_rect(), Qt.AlignmentFlag.AlignCenter, self.text())

    def mousePressEvent(self, ev):
        if self._on_click and ev.button() == Qt.MouseButton.LeftButton:
            self._on_click()


def tag_chip(tag: str, on_click=None) -> ClickChip:
    return ClickChip(tag, T.tag_color(tag), T.BORDER_FIELD, px=10, hpad=6,
                     on_click=on_click, ls=0)


class Switch(QAbstractButton):
    """30x17 sliding toggle (settings rows, show-completed)."""

    def __init__(self, checked: bool = False):
        super().__init__()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(T.sc(30), T.sc(17))
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(T.ACCENT) if self.isChecked()
                   else qcolor(T.BORDER_STRONG))
        # Proportional to the widget, which the font scale resizes — drawing at
        # the design's own 30x17 would leave the switch stranded in one corner.
        w, h = self.width(), self.height()
        p.drawRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        inset = h * 0.12
        knob = h - 2 * inset
        x = w - knob - inset if self.isChecked() else inset
        p.setBrush(qcolor(T.BG_MAIN))
        p.drawEllipse(QRectF(x, inset, knob, knob))


class TodoCheck(QAbstractButton):
    """16x16 rounded checkbox from the todo rows."""

    def __init__(self, checked: bool = False):
        super().__init__()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(T.sc(16), T.sc(16))
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.isChecked():
            p.setBrush(qcolor(T.ACCENT))
            p.setPen(QPen(qcolor(T.ACCENT), 1))
            p.drawRoundedRect(r, 3, 3)
            # Stroked, not a "✓" glyph: the substituted fonts render U+2713 as
            # a lowercase-v shape that reads as a letter, not a checkmark.
            pen = QPen(qcolor(T.ACCENT_ON), 1.8 * T.FONT_SCALE)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            k = self.width() / 16      # the tick was drawn for a 16px box
            p.drawPolyline(QPolygonF([QPointF(4.0 * k, 8.2 * k),
                                      QPointF(6.7 * k, 11.0 * k),
                                      QPointF(12.0 * k, 5.2 * k)]))
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(qcolor(T.TEXT_GHOST), 1))
            p.drawRoundedRect(r, 3, 3)


class Avatar(QWidget):
    """Circular sender initial in the reading pane (36px, accent-tinted)."""

    def __init__(self, initial: str, size: int = 36):
        super().__init__()
        size = T.sc(size)
        self.setFixedSize(size, size)
        self._initial = initial
        self._size = size

    def set_initial(self, initial: str):
        self._initial = initial
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(qcolor(T.ACCENT_SOFT))
        p.setPen(QPen(qcolor(T.ACCENT), 1))
        p.drawEllipse(r)
        p.setPen(qcolor(T.ACCENT))
        p.setFont(font(self._size * 0.44, 600))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._initial)


class ClickRow(QFrame):
    """Row container that fires a callback on click (list rows, nav, cards)."""

    def __init__(self, on_click=None):
        super().__init__()
        self._on_click = on_click
        if on_click:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, ev):
        if self._on_click and ev.button() == Qt.MouseButton.LeftButton:
            self._on_click()
        super().mousePressEvent(ev)


class ClickLabel(QLabel):
    """Clickable text/glyph (✕ delete, 'Manage todos →', breadcrumbs)."""

    def __init__(self, text: str, px: float, color: str, on_click=None,
                 tooltip: str = "", weight: int = 400, mono: bool = False):
        super().__init__(text)
        self.setFont(font(px, weight, mono))
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.WindowText, qcolor(color))
        self.setPalette(pal)
        if on_click:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)
        self._on_click = on_click

    def mousePressEvent(self, ev):
        if self._on_click and ev.button() == Qt.MouseButton.LeftButton:
            self._on_click()


class AccentBar(QWidget):
    """The 2.5px left rule the mockup puts on selected rows and event blocks.
    Kept as a widget (not a QSS border) so rows can flip it without a repolish."""

    def __init__(self, on: bool = False, w: float = 2.5, color: str | None = None):
        super().__init__()
        self.setFixedWidth(int(w) + 1)
        self._on, self._w = on, w
        self._color = color

    def set_on(self, on: bool, color: str | None = None):
        self._on = on
        if color is not None:
            self._color = color
        self.update()

    def paintEvent(self, ev):
        if not self._on:
            return
        p = QPainter(self)
        p.fillRect(QRectF(0, 0, self._w, self.height()),
                   qcolor(self._color or T.ACCENT))


class ProgressBar(QWidget):
    """5px rounded progress track from the todos rail."""

    def __init__(self, pct: float = 0.0):
        super().__init__()
        self.setFixedHeight(T.sc(5))
        self._pct = pct

    def set_pct(self, pct: float):
        self._pct = max(0.0, min(1.0, pct))
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(T.BORDER_TRACK))
        p.drawRoundedRect(QRectF(0, 0, self.width(), 5), 3, 3)
        if self._pct > 0:
            p.setBrush(qcolor(T.ACCENT))
            p.drawRoundedRect(QRectF(0, 0, self.width() * self._pct, 5), 3, 3)


class HtmlBody(QTextBrowser):
    """HTML mail rendered on a white 'paper' card (HTML mail assumes a light
    background), sized to its document so the reading pane's outer scroll area
    does all the scrolling. QTextBrowser runs no scripts and fetches no remote
    resources, so opening a message leaks nothing; links open externally."""

    def __init__(self, html: str):
        super().__init__()
        self.setOpenExternalLinks(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet(
            "QTextBrowser { background: #ffffff; color: #1f1f1f; "
            f"border: 1px solid {T.BORDER_FIELD}; border-radius: 6px; "
            "padding: 12px; }")
        self.document().setDefaultFont(font(14))
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        self.setHtml(html)
        self.document().documentLayout().documentSizeChanged.connect(
            lambda _s: self._fit())

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self.document().setTextWidth(self.viewport().width())
        self._fit()

    def _fit(self):
        h = max(int(self.document().size().height()) + 28, 40)
        if h != self.height():
            self.setFixedHeight(h)


class FlowLayout(QLayout):
    """Left-aligned wrapping layout (filter chip rows, label pills)."""

    def __init__(self, parent=None, hgap: int = 5, vgap: int = 5):
        super().__init__(parent)
        self._items, self._h, self._v = [], hgap, vgap
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._arrange(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _arrange(self, rect, test_only: bool) -> int:
        x, y, line_h = rect.x(), rect.y(), 0
        for it in self._items:
            w, h = it.sizeHint().width(), it.sizeHint().height()
            if x + w > rect.right() + 1 and line_h > 0:
                x, y, line_h = rect.x(), y + line_h + self._v, 0
            if not test_only:
                it.setGeometry(QRect(QPoint(x, y), it.sizeHint()))
            x += w + self._h
            line_h = max(line_h, h)
        return y + line_h - rect.y()
