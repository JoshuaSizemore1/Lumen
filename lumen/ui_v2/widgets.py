"""Shared building blocks: text helpers, chips, dots, switches, rows."""
from PyQt6.QtCore import Qt, QPoint, QRect, QSize, QRectF
from PyQt6.QtGui import QColor, QFont, QPainter, QPalette, QPen
from PyQt6.QtWidgets import (
    QAbstractButton, QFrame, QHBoxLayout, QLabel, QLayout, QPushButton,
    QScrollArea, QSizePolicy, QTextBrowser, QVBoxLayout, QWidget,
)

from . import theme as T


# ---------------------------------------------------------------- helpers
def qcolor(spec: str, alpha: int | None = None) -> QColor:
    """CSS-style hex (#rrggbb or #rrggbbaa, alpha LAST) -> QColor."""
    if len(spec) == 9:  # #rrggbbaa
        c = QColor(spec[:7])
        c.setAlpha(int(spec[7:9], 16))
        return c
    c = QColor(spec)
    if alpha is not None:
        c.setAlpha(alpha)
    return c


def font(px: int, weight: int = 400, sans: bool = False, ls: float = 0.0) -> QFont:
    f = QFont(T.FONT_SANS if sans else T.FONT_MONO)
    f.setPixelSize(px)
    f.setWeight(QFont.Weight(weight))
    if ls:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, ls)
    return f


def label(text: str, px: int, color: str, weight: int = 400, sans: bool = False,
          ls: float = 0.0, wrap: bool = False) -> QLabel:
    w = QLabel(text)
    w.setFont(font(px, weight, sans, ls))
    pal = w.palette()
    pal.setColor(QPalette.ColorRole.WindowText, qcolor(color))
    w.setPalette(pal)
    if wrap:
        w.setWordWrap(True)
    return w


class ElideLabel(QLabel):
    """Single-line label that elides with '…' instead of forcing width."""

    def __init__(self, text: str, px: int, color: str, weight: int = 400,
                 sans: bool = False):
        super().__init__(text)
        self.setFont(font(px, weight, sans))
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.WindowText, qcolor(color))
        self.setPalette(pal)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(24)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setFont(self.font())
        p.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        t = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width())
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, t)


def hbox(host: QWidget | None = None, m=(0, 0, 0, 0), s: int = 0) -> QHBoxLayout:
    lay = QHBoxLayout(host) if host else QHBoxLayout()
    lay.setContentsMargins(*m)
    lay.setSpacing(s)
    return lay


def vbox(host: QWidget | None = None, m=(0, 0, 0, 0), s: int = 0) -> QVBoxLayout:
    lay = QVBoxLayout(host) if host else QVBoxLayout()
    lay.setContentsMargins(*m)
    lay.setSpacing(s)
    return lay


def clear_layout(lay):
    while lay.count():
        item = lay.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)  # detach now, not on next event-loop tick
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


def vline(color: str = T.BORDER_SOFT) -> QFrame:
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


class HtmlBody(QTextBrowser):
    """HTML email body rendered on a white "paper" card (HTML mail assumes a
    light background), sized to its document so the reading pane's outer
    scroll area does all the scrolling — no nested scrollbars. QTextBrowser
    cannot run scripts and never fetches remote resources, so opening a
    message leaks nothing; links open in the system browser."""

    def __init__(self, html: str):
        super().__init__()
        self.setOpenExternalLinks(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet(
            "QTextBrowser { background: #ffffff; color: #1f1f1f; "
            f"border: 1px solid {T.BORDER_SOFT}; border-radius: 8px; "
            "padding: 10px; }")
        self.document().setDefaultFont(font(13, sans=True))
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
        h = max(int(self.document().size().height()) + 26, 40)
        if h != self.height():
            self.setFixedHeight(h)


def empty_state(text: str, sub: str | None = None) -> QWidget:
    """Centered muted placeholder for offline/loading/empty/error panes.
    One helper so wording and styling stay consistent across screens."""
    w = QWidget()
    lay = vbox(w, (0, 0, 0, 0), 4)
    lay.addStretch(1)
    title = label(text, 13, T.TEXT_DIM)
    title.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lay.addWidget(title)
    if sub:
        s = label(sub, 11, T.TEXT_FAINT)
        s.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(s)
    lay.addStretch(1)
    return w


def button(text: str, variant: str, px: int | None = None, height: int | None = None) -> QPushButton:
    b = QPushButton(text)
    b.setProperty("variant", variant)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if px is not None:
        f = b.font()
        f.setPixelSize(px)
        b.setFont(f)
    if height is not None:
        b.setFixedHeight(height)
    return b


def seg_button(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setProperty("cls", "seg")
    b.setCheckable(True)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


# ---------------------------------------------------------------- widgets
class Dot(QWidget):
    """Fixed-size colored dot / square (status dots, legend swatches)."""

    def __init__(self, size: int, color: str, radius: float | None = None,
                 border: str | None = None):
        super().__init__()
        self.setFixedSize(size, size)
        self._color = color
        self._radius = size / 2 if radius is None else radius
        self._border = border

    def set_color(self, color: str, border: str | None = None):
        self._color = color
        self._border = border
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self._color and self._color != "transparent":
            p.setBrush(qcolor(self._color))
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(qcolor(self._border), 1) if self._border else Qt.PenStyle.NoPen)
        p.drawRoundedRect(r, self._radius, self._radius)


class Chip(QLabel):
    """Bordered pill/chip label (tags, kbd hints, badges)."""

    def __init__(self, text: str, fg: str, border: str, bg: str | None = None,
                 px: int = 10, radius: int = 3, hpad: int = 5, vpad: int = 1,
                 weight: int = 400):
        super().__init__(text)
        self._fg, self._border, self._bg = fg, border, bg
        self._radius, self._hpad, self._vpad = radius, hpad, vpad
        self.setFont(font(px, weight))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def restyle(self, fg: str, border: str, bg: str | None = None):
        self._fg, self._border, self._bg = fg, border, bg
        self.update()

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        return QSize(fm.horizontalAdvance(self.text()) + 2 * self._hpad + 2,
                     fm.height() + 2 * self._vpad + 2)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(qcolor(self._bg) if self._bg else Qt.BrushStyle.NoBrush)
        p.setPen(QPen(qcolor(self._border), 1) if self._border else Qt.PenStyle.NoPen)
        p.drawRoundedRect(r, self._radius, self._radius)
        p.setPen(qcolor(self._fg))
        p.setFont(self.font())
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())


def tag_chip(tag: str) -> Chip:
    return Chip(tag, T.TAG_COLORS.get(tag, T.TEXT_DIM), T.BORDER_STRONG)


class ClickChip(Chip):
    """Chip that emits a callback on click (mail filter chips, suggestions)."""

    def __init__(self, text: str, fg: str, border: str, bg: str | None = None,
                 px: int = 10, on_click=None, tooltip: str = ""):
        super().__init__(text, fg, border, bg, px=px, radius=8, hpad=8, vpad=3)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)
        self._on_click = on_click

    def mousePressEvent(self, ev):
        if self._on_click and ev.button() == Qt.MouseButton.LeftButton:
            self._on_click()


class FlowLayout(QLayout):
    """Left-aligned wrapping layout (label chip rows in a fixed-width column)."""

    def __init__(self, parent=None, hgap: int = 6, vgap: int = 6):
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


class Switch(QAbstractButton):
    """30x17 sliding toggle from the settings screen."""

    def __init__(self, checked: bool = False):
        super().__init__()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(30, 17)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(T.ACCENT) if self.isChecked() else qcolor(T.BORDER_STRONG))
        p.drawRoundedRect(QRectF(0, 0, 30, 17), 8.5, 8.5)
        x = 15 if self.isChecked() else 2
        p.setBrush(qcolor(T.BG_APP))
        p.drawEllipse(QRectF(x, 2, 13, 13))


class TodoCheck(QAbstractButton):
    """15x15 rounded check box from the todo rows."""

    def __init__(self, checked: bool = False):
        super().__init__()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(15, 15)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.isChecked():
            p.setBrush(qcolor(T.ACCENT))
            p.setPen(QPen(qcolor(T.ACCENT), 1))
            p.drawRoundedRect(r, 3, 3)
            p.setPen(qcolor(T.BG_WINDOW))
            p.setFont(font(10, 700))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "✓")
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(qcolor(T.TEXT_FAINT), 1))
            p.drawRoundedRect(r, 3, 3)


class ClickRow(QFrame):
    """Row container that emits a callback on click (list rows, try rows)."""

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
    """Clickable glyph label (todo delete ✕)."""

    def __init__(self, text: str, px: int, color: str, on_click=None, tooltip: str = ""):
        super().__init__(text)
        self.setFont(font(px))
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.WindowText, qcolor(color))
        self.setPalette(pal)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)
        self._on_click = on_click

    def mousePressEvent(self, ev):
        if self._on_click and ev.button() == Qt.MouseButton.LeftButton:
            self._on_click()


class CompositeButton(QPushButton):
    """Button whose face is a small layout (main text + colored hint)."""

    def __init__(self, variant: str, parts: list[QLabel], height: int | None = None,
                 spacing: int = 6):
        super().__init__()
        self.setProperty("variant", variant)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = hbox(self, (0, 0, 0, 0), spacing)
        lay.addStretch(1)
        for part in parts:
            part.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            lay.addWidget(part)
        lay.addStretch(1)
        if height is not None:
            self.setFixedHeight(height)
