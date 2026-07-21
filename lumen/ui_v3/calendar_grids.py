"""Time-proportional calendar grids: month cells, week/day columns, and the
dense Today mini-schedule.

These are the one place the mockup's `position:absolute` is load-bearing — an
event's top and height *are* its start and duration, which no Qt layout can
express. Blocks are therefore parentless-of-layout children positioned in
`resizeEvent`; everything around them still uses real layouts.
"""
from datetime import date, timedelta

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QPainter, QPen
from PyQt6.QtWidgets import QFrame, QGridLayout, QWidget

from . import theme as T
from .widgets import (
    ClickRow, ElideLabel, hbox, label, qcolor, vbox,
)

WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")
MON3 = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
        "Nov", "Dec")
WD_FULL = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
           "Sunday")


def to_min(hhmm: str) -> int:
    h, _, m = hhmm.partition(":")
    return int(h) * 60 + int(m or 0)


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


class EventBlock(QFrame):
    """A single event, tinted by its calendar color: 11% fill, 2.5px left rule."""

    # A block shorter than this can't fit both a title line and a meta line, so
    # the meta line is dropped rather than clipped mid-glyph (#17).
    META_MIN_H = 30

    def __init__(self, ev: dict, compact: bool = False, on_click=None):
        super().__init__()
        self.ev = ev
        self.color = T.cal_color(ev)
        self._on_click = on_click
        if on_click:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        pad = (7, 2, 5, 1) if compact else (10, 3, 8, 2)
        v = vbox(self, pad, 0)
        self._title = ElideLabel(ev.get("title", "Untitled"),
                                 10 if compact else 11.5, T.TEXT_PRIMARY, 600)
        v.addWidget(self._title)
        meta = ev.get("start", "")
        if not compact and ev.get("cal"):
            meta = f"{meta} · {ev['cal']}"
        self._meta = None
        if meta:
            self._meta = ElideLabel(meta, 8.5 if compact else 10, self.color,
                                    mono=True)
            v.addWidget(self._meta)
        v.addStretch(1)
        # The full title + time is always available on hover, so nothing is
        # ever truly lost even in a tiny rectangle (#17).
        when = ev.get("start", "")
        cal = f" · {ev['cal']}" if ev.get("cal") else ""
        self.setToolTip(f"{ev.get('title', 'Untitled')}"
                        + (f"\n{when}{cal}" if when else ""))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # Hide the second (meta) line on a short block so the title alone gets
        # the room and is never sliced through the middle (#17).
        if self._meta is not None:
            self._meta.setVisible(self.height() >= self.META_MIN_H)

    def mousePressEvent(self, e):
        if self._on_click and e.button() == Qt.MouseButton.LeftButton:
            self._on_click(self.ev)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(self.color, round(T.TINT_EVENT * 255)))
        p.drawRoundedRect(r, 4, 4)
        p.setBrush(qcolor(self.color))
        p.drawRect(QRectF(0, 0, 2.5, self.height()))


class DayColumn(QWidget):
    """One day's worth of time: hour rules painted, event blocks placed by time."""

    def __init__(self, events: list[dict], hour_h: int = T.GRID_HOUR_H,
                 start_h: int = T.GRID_START_H, end_h: int = T.GRID_END_H,
                 compact: bool = True, left_rule: bool = True,
                 gutter: int = 0, on_event=None, min_h: int = 22,
                 shrink: int = 2, label_px: float = 0):
        super().__init__()
        self._hour_h, self._start_h, self._end_h = hour_h, start_h, end_h
        self._left_rule = left_rule
        self._gutter = gutter
        self._min_h, self._shrink = min_h, shrink
        # >0 makes the column paint its own hour labels in the gutter, which is
        # how the Today mini-schedule works (no separate TimeGutter widget).
        self._label_px = label_px
        self._now_min: int | None = None
        self.setMinimumHeight((end_h - start_h) * hour_h)
        self.blocks = [EventBlock(e, compact, on_event)
                       for e in events if not e.get("all_day")]
        for b in self.blocks:
            b.setParent(self)

    def set_now(self, minute: int | None):
        self._now_min = minute
        self.update()

    def _geom(self, ev: dict) -> tuple[int, int]:
        top = max((ev.get("start_min", 0) - self._start_h * 60)
                  / 60 * self._hour_h, 0)
        h = max(ev.get("dur", 30) / 60 * self._hour_h, self._min_h) - self._shrink
        return int(top), int(h)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        x = self._gutter + 3
        w = max(self.width() - self._gutter - 6, 10)
        for b in self.blocks:
            top, h = self._geom(b.ev)
            b.setGeometry(x, top, w, h)

    def paintEvent(self, e):
        from .widgets import font
        p = QPainter(self)
        p.setPen(QPen(qcolor(T.BORDER_FAINT), 1))
        for i in range(self._end_h - self._start_h + 1):
            y = i * self._hour_h
            p.drawLine(self._gutter, y, self.width(), y)
        if self._left_rule:
            p.drawLine(self._gutter, 0, self._gutter, self.height())
        if self._label_px:
            p.setFont(font(self._label_px, mono=True))
            p.setPen(qcolor(T.TEXT_FAINTER))
            for i in range(self._end_h - self._start_h + 1):
                hour = self._start_h + i
                p.drawText(
                    QRectF(0, i * self._hour_h - 7, self._gutter - 8, 14),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    f"{hour:02d}:00")
        if self._now_min is not None:
            y = (self._now_min - self._start_h * 60) / 60 * self._hour_h
            p.setPen(QPen(qcolor(T.ACCENT), 1.5))
            p.drawLine(self._gutter + 2, int(y), self.width(), int(y))


class TimeGutter(QWidget):
    """Right-aligned hour labels down the left edge of a time grid."""

    def __init__(self, width: int, hour_h: int = T.GRID_HOUR_H,
                 start_h: int = T.GRID_START_H, end_h: int = T.GRID_END_H,
                 px: float = 9, pad_right: int = 8):
        super().__init__()
        self._hour_h, self._start_h, self._end_h = hour_h, start_h, end_h
        self._px, self._pad = px, pad_right
        self.setFixedWidth(width)
        self.setMinimumHeight((end_h - start_h) * hour_h)

    def paintEvent(self, e):
        from .widgets import font
        p = QPainter(self)
        p.setFont(font(self._px, mono=True))
        p.setPen(qcolor(T.TEXT_FAINTER))
        for i in range(self._end_h - self._start_h + 1):
            hour = self._start_h + i
            y = i * self._hour_h
            p.drawText(QRectF(0, y - 7, self.width() - self._pad, 14),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{hour:02d}:00")


class MonthCell(ClickRow):
    """One month-grid day: date number, up to three event chips, '+N more'."""

    CHIP_H = 17

    def __init__(self, day: date, events: list[dict], in_month: bool,
                 is_today: bool, on_click=None):
        super().__init__(on_click)
        self.setMinimumHeight(T.MONTH_CELL_MIN_H)
        self._in_month, self._is_today = in_month, is_today
        v = vbox(self, (7, 6, 7, 4), 1)

        num = label(str(day.day), 11, T.ACCENT_ON if is_today
                    else (T.TEXT_PRIMARY if in_month else T.TEXT_FAINTER),
                    700 if is_today else 400, mono=True)
        num.setAlignment(Qt.AlignmentFlag.AlignCenter)
        num.setFixedSize(T.sc(20), T.sc(20))
        if is_today:
            r, g, b = (int(T.ACCENT[i:i + 2], 16) for i in (1, 3, 5))
            num.setStyleSheet(
                f"background: rgb({r},{g},{b}); border-radius: 10px;")
        row = hbox(m=(0, 0, 0, 3), s=0)
        row.addWidget(num)
        row.addStretch(1)
        v.addLayout(row)

        shown = sorted(events, key=lambda e: e.get("start_min", 0))[:3]
        for ev in shown:
            v.addWidget(self._chip(ev))
        more = len(events) - len(shown)
        if more > 0:
            v.addWidget(label(f"+{more} more", 9, T.TEXT_FAINT, mono=True))
        v.addStretch(1)

    def _chip(self, ev: dict) -> QWidget:
        color = T.cal_color(ev)
        w = ElideLabel(ev.get("title", ""), 10,
                       T.TEXT_BODY if self._in_month else T.TEXT_OUT_MONTH)
        w.setFixedHeight(T.sc(self.CHIP_H))
        # Margins, not QSS padding: ElideLabel paints into contentsRect, and
        # padding alone would let the text run under the left colour rule.
        w.setContentsMargins(7, 1, 5, 1)
        r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
        a = T.TINT_EVENT if self._in_month else T.TINT_EVENT * 0.57
        w.setStyleSheet(
            f"background: rgba({r},{g},{b},{a:.2f});"
            f"border-left: 2px solid {color}; border-radius: 3px;")
        return w

    def paintEvent(self, e):
        p = QPainter(self)
        if self._is_today:
            r, g, b = (int(T.ACCENT[i:i + 2], 16) for i in (1, 3, 5))
            p.fillRect(self.rect(), qcolor(T.ACCENT_SOFT))
        elif not self._in_month:
            p.fillRect(self.rect(), qcolor(T.BG_OUT_MONTH))
        p.setPen(QPen(qcolor(T.BORDER_FAINT), 1))
        p.drawRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5))


class MonthGrid(QWidget):
    """6x7 month view. Columns share width evenly so the grid fills any width."""

    def __init__(self, anchor: date, events: list[dict], today: date,
                 on_day=None):
        super().__init__()
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        by_day: dict[str, list[dict]] = {}
        for ev in events:
            by_day.setdefault(ev["date"], []).append(ev)

        first = anchor.replace(day=1)
        start = monday_of(first)
        for w in range(6):
            for d in range(7):
                day = start + timedelta(days=w * 7 + d)
                cell = MonthCell(
                    day, by_day.get(day.isoformat(), []),
                    in_month=(day.month == anchor.month),
                    is_today=(day == today),
                    on_click=(lambda dd=day: on_day(dd)) if on_day else None)
                grid.addWidget(cell, w, d)
        for c in range(7):
            grid.setColumnStretch(c, 1)
        for r in range(6):
            grid.setRowStretch(r, 1)
