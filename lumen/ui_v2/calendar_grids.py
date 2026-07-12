"""Custom-painted calendar surfaces (month grid, week/day time grids).

The mock positions event blocks absolutely over hour grids; here that is
painting math. Hour row height scales up from the mock's base value when the
window grows, so these widgets absorb vertical growth.
"""
from datetime import datetime

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

from . import theme as T
from .widgets import font, qcolor


def _to_min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


class MonthGrid(QWidget):
    """6x7 month cells with event chips; emits day_clicked(iso)."""

    day_clicked = pyqtSignal(str)
    MIN_ROW = 104

    def __init__(self):
        super().__init__()
        self.weeks: list[list[dict]] = []
        self.setMinimumHeight(6 * self.MIN_ROW)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_weeks(self, weeks: list[list[dict]]):
        self.weeks = weeks
        self.update()

    def _cell_at(self, x: int, y: int):
        if not self.weeks:
            return None
        row_h = max(self.MIN_ROW, self.height() // 6)
        r = min(int(y // row_h), 5)
        c = min(int(x / (self.width() / 7)), 6)
        return self.weeks[r][c]

    def mousePressEvent(self, ev):
        cell = self._cell_at(int(ev.position().x()), int(ev.position().y()))
        if cell:
            self.day_clicked.emit(cell["iso"])

    def paintEvent(self, ev):
        if not self.weeks:
            return
        p = QPainter(self)
        w, h = self.width(), self.height()
        row_h = max(self.MIN_ROW, h // 6)
        col_w = w / 7
        for r, week in enumerate(self.weeks):
            for c, d in enumerate(week):
                x0, y0 = round(c * col_w), r * row_h
                x1 = round((c + 1) * col_w)
                # cell background
                if d["is_today"]:
                    p.fillRect(x0, y0, x1 - x0, row_h, qcolor(T.ACCENT, 0x1F))
                elif not d["in_month"]:
                    p.fillRect(x0, y0, x1 - x0, row_h, qcolor("#141520"))
                # grid lines
                p.setPen(QPen(qcolor(T.BORDER_FAINT), 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(x0, y0, x1 - x0, row_h)
                # day number (padding 5px 6px)
                nx, ny = x0 + 6, y0 + 5
                if d["is_today"]:
                    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(qcolor(T.ACCENT))
                    p.drawEllipse(nx, ny, 19, 19)
                    p.setPen(qcolor(T.ACCENT_ON))
                    p.setFont(font(11, 700))
                    p.drawText(nx, ny, 19, 19, Qt.AlignmentFlag.AlignCenter, str(d["num"]))
                    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
                else:
                    p.setPen(qcolor(T.TEXT_PRIMARY if d["in_month"] else T.TEXT_FAINT))
                    p.setFont(font(11))
                    p.drawText(nx, ny, 19, 19, Qt.AlignmentFlag.AlignCenter, str(d["num"]))
                # event chips
                cy = ny + 19 + 2
                chip_w = (x1 - x0) - 12
                p.setFont(font(10))
                fm = p.fontMetrics()
                for title, color in d["chips"]:
                    if cy + 16 > y0 + row_h - 4:
                        break
                    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(qcolor(color, 0x1F if d["in_month"] else 0x12))
                    p.drawRoundedRect(x0 + 6, cy, chip_w, 16, 3, 3)
                    p.setBrush(qcolor(color))
                    p.drawRect(x0 + 6, cy, 2, 16)
                    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
                    p.setPen(qcolor("#c7cdf0" if d["in_month"] else "#4a5170"))
                    t = fm.elidedText(title, Qt.TextElideMode.ElideRight, chip_w - 10)
                    p.drawText(x0 + 11, cy, chip_w - 10, 16,
                               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, t)
                    cy += 18
                if d["more"]:
                    p.setPen(qcolor(T.TEXT_DIM))
                    p.setFont(font(9))
                    p.drawText(x0 + 8, cy, chip_w - 4, 13,
                               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                               f"+{d['more']} more")
                    p.setFont(font(10))


class TimeGrid(QWidget):
    """Hour grid with positioned event blocks (calendar week/day views).

    columns: list of lists of event dicts {start_min, dur, title, time, cal}.
    One column = day view; seven = week view (compact type).
    """

    def __init__(self, gutter: int, start_h: int, end_h: int, base_hh: int,
                 compact: bool, label_px: int):
        super().__init__()
        self.gutter, self.start_h, self.end_h = gutter, start_h, end_h
        self.base_hh, self.compact, self.label_px = base_hh, compact, label_px
        self.columns: list[list[dict]] = []
        n_hours = end_h - start_h
        self.setMinimumHeight(n_hours * base_hh)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_columns(self, columns: list[list[dict]]):
        self.columns = columns
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        w, h = self.width(), self.height()
        n_hours = self.end_h - self.start_h
        hh = max(self.base_hh, h / n_hours)
        ncols = max(len(self.columns), 1)
        col_w = (w - self.gutter) / ncols

        # hour lines + gutter labels
        p.setFont(font(self.label_px))
        for k in range(n_hours + 1):
            y = round(k * hh)
            if k > 0:
                p.setPen(QPen(qcolor(T.BORDER_FAINT), 1))
                p.drawLine(0, y - 1, w, y - 1)
            p.setPen(qcolor(T.TEXT_FAINT))
            p.drawText(0, y - 6, self.gutter - (8 if self.compact else 10), 12,
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{self.start_h + k:02d}:00")

        # column separators
        p.setPen(QPen(qcolor(T.BORDER_FAINT), 1))
        for c in range(ncols):
            x = round(self.gutter + c * col_w)
            p.drawLine(x, 0, x, h)

        # event blocks
        pad_x = 4 if self.compact else 8
        pad_y = 2 if self.compact else 4
        title_px = 9 if self.compact else 11
        meta_px = 8 if self.compact else 9
        for c, events in enumerate(self.columns):
            cx = self.gutter + c * col_w
            for e in events:
                color = e.get("color") or T.CAL_COLORS.get(e.get("cal"), T.TEXT_DIM)
                top = max((e["start_min"] - self.start_h * 60) / 60 * hh, 0)
                bh = max(e["dur"] / 60 * hh - 2, 15)
                bx, bw = cx + 3, col_w - 6
                p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(qcolor(color, 0x26))
                p.drawRoundedRect(int(bx), int(top), int(bw), int(bh), 4, 4)
                p.setBrush(qcolor(color))
                p.drawRect(int(bx), int(top), 2, int(bh))
                p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
                p.save()
                p.setClipRect(int(bx), int(top), int(bw), int(bh))
                p.setPen(qcolor("#dfe4f5"))
                p.setFont(font(title_px, 500))
                fm = p.fontMetrics()
                t = fm.elidedText(e["title"], Qt.TextElideMode.ElideRight, int(bw - 2 * pad_x))
                p.drawText(int(bx + pad_x), int(top + pad_y), int(bw - 2 * pad_x), fm.height(),
                           Qt.AlignmentFlag.AlignLeft, t)
                p.setPen(qcolor(color))
                p.setFont(font(meta_px))
                meta = e["time"] if self.compact else f"{e['time']} · {e['cal']}"
                p.drawText(int(bx + pad_x), int(top + pad_y + fm.height()),
                           int(bw - 2 * pad_x), 12, Qt.AlignmentFlag.AlignLeft, meta)
                p.restore()


class DashDayGrid(QWidget):
    """Dashboard 'today' hour panel: rounded card, hour rows, now line, blocks."""

    START, END, BASE_HH = 8, 20, 50

    def __init__(self):
        super().__init__()
        self.events: list[dict] = []  # {start_min, dur_min, title, time, dur, next}
        self.setMinimumHeight((self.END - self.START) * self.BASE_HH + 12)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_events(self, events: list[dict]):
        self.events = events
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QPen(qcolor(T.BORDER_SOFT), 1))
        p.setBrush(qcolor(T.BG_PANEL_ALT))
        p.drawRoundedRect(0, 0, w - 1, h - 1, 8, 8)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        n_hours = self.END - self.START
        hh = max(self.BASE_HH, (h - 12) / n_hours)
        top0 = 6

        # hour rows: right-aligned label (36px wide at x=10) + hairline
        p.setFont(font(9))
        for k in range(n_hours + 1):
            y = top0 + round(k * hh)
            p.setPen(qcolor(T.TEXT_FAINT))
            p.drawText(10, y - 6, 36, 12,
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{self.START + k:02d}:00")
            p.setPen(QPen(qcolor(T.BORDER_FAINT), 1))
            p.drawLine(54, y, w - 10, y)

        # event blocks (left 52, right 10)
        for e in self.events:
            top = top0 + (e["start_min"] - self.START * 60) / 60 * hh
            bh = max(e["dur_min"] / 60 * hh, 24) - 3
            bx, bw = 52, w - 52 - 10
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qcolor(T.ACCENT, 0x33 if e["next"] else 0x1F))
            p.drawRoundedRect(int(bx), int(top), int(bw), int(bh), 5, 5)
            p.setBrush(qcolor(T.ACCENT))
            p.drawRect(int(bx), int(top), 2, int(bh))
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            p.save()
            p.setClipRect(int(bx), int(top), int(bw), int(bh))
            p.setPen(qcolor(T.TEXT_PRIMARY))
            p.setFont(font(11, 500))
            fm = p.fontMetrics()
            t = fm.elidedText(e["title"], Qt.TextElideMode.ElideRight, int(bw - 18))
            p.drawText(int(bx + 9), int(top + 4), int(bw - 18), fm.height(),
                       Qt.AlignmentFlag.AlignLeft, t)
            p.setPen(qcolor(T.ACCENT))
            p.setFont(font(9))
            p.drawText(int(bx + 9), int(top + 4 + fm.height()), int(bw - 18), 12,
                       Qt.AlignmentFlag.AlignLeft, f"{e['time']} · {e['dur']}")
            p.restore()

        # now line (real current time, shown only when within the visible band)
        now = datetime.now()
        now_min = now.hour * 60 + now.minute
        if self.START * 60 <= now_min <= self.END * 60:
            ny = top0 + round((now_min - self.START * 60) / 60 * hh)
            p.setPen(QPen(qcolor(T.NOW), 1))
            p.drawLine(46, ny, w - 10, ny)
            p.setFont(font(8))
            p.drawText(12, ny - 6, 34, 12,
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       now.strftime("%H:%M"))
