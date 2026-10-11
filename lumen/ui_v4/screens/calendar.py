"""Calendar: month, week and day views, restyled in Field Notes.

Keeps everything the ui_v3 calendar did:
- month / week / day views with previous / today / next navigation
- clicking a month cell or week day opens that day
- a day view with an agenda where events can be edited (title, time,
  colour) or deleted. Both writes go to the daemon, which asks you to
  confirm before it changes Google Calendar.
- a sync button that always does a real Google sync, plus the debounced
  sync each time the screen is shown (#59)
- in-page back/forward (nav_token / nav_restore + nav_location_changed, #18)
- a "not connected" state

New in v4:
- a loading skeleton
- a legend that follows the calendars actually on screen
- overlapping events sit side by side instead of on top of each other
- double-click an empty time slot to start a new event at that time
- the grid grows past 07:00-21:00 when an event falls outside that range
- the day view's agenda drops below the grid when the screen is narrow

Event colours are Google's own, as ui_v3 painted them: the event's colour if
it has one, else its calendar's (both arrive as data). Sample events, which
carry none, fall back to the theme's categorical slots, hashed from the
calendar name.
"""
from datetime import date, datetime, timedelta

from PyQt6.QtCore import QEvent, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QComboBox, QGridLayout, QSizePolicy, QSplitter, QWidget,
)

from .. import theme as T
from ..components import (
    Badge, Button, ClickRow, Dot, ElideLabel, EmptyState, Eyebrow, IconButton,
    Label, ScreenHeader, ScrollArea, SegmentedControl, SkeletonRow, TextField,
    clear_layout, fire_on_next_tick, hbox, vbox,
)
from ..overlays import (
    EventOverlay, _dialog_foot, _dialog_head, _Overlay, parse_clock,
)

from ...daemon.connectors.gcal_colors import GCAL_COLOR_NAMES
from ...daemon.connectors.gcal_time import tz_offset
from ...ui_v2.state import expand_events

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
WD_FULL = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
           "Sunday")
MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")
MON3 = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
        "Nov", "Dec")

GRID_START_H, GRID_END_H = 7, 21
HOUR_H = 48
PAD_T = 10                 # room above the first hour line for its label
NARROW_W = 900


# ---- helpers ----------------------------------------------------------------
def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _short_day(iso: str) -> str:
    """"Sep 11" from an ISO date; the text unchanged if it isn't one."""
    try:
        d = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return str(iso)
    return f"{d.strftime('%b')} {d.day}"


def event_color(ev: dict) -> str:
    """Google's colour for the event (its own, else its calendar's), or a
    categorical slot stable per calendar when there's none (L15)."""
    c = ev.get("color")
    if isinstance(c, str) and c.startswith("#") and QColor(c).isValid():
        return c
    return T.tag_color(ev.get("cal") or "event")


def calendar_color(ev: dict) -> str:
    """The event's calendar's colour, for the legend."""
    c = ev.get("cal_color")
    if isinstance(c, str) and c.startswith("#") and QColor(c).isValid():
        return c
    return event_color(ev)


def hhmm(minutes: int) -> str:
    minutes = max(0, min(24 * 60, int(minutes)))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _whole(ev: dict) -> tuple[str, int, int]:
    """(start date, start minute, length) of the whole event. One day's piece
    of a multi-day event carries them as `_event_*` (#113); the edit dialog
    must change the real event, never the piece."""
    return (ev.get("_event_date") or ev["date"],
            ev.get("_event_start_min", ev.get("start_min", 0)),
            ev.get("_event_dur", ev.get("dur", 30)))


def _end_hhmm(ev: dict) -> str:
    """The end time as the edit dialog shows it: "24:00" for midnight, and
    the clock time for an event that runs past it (the dialog reads an end
    at or before the start as the next day)."""
    _day, start, dur = _whole(ev)
    end = start + dur
    return "24:00" if end == 24 * 60 else hhmm(end % (24 * 60))


def _minutes(value: str) -> int:
    h, _, m = value.partition(":")
    return int(h) * 60 + int(m)


def time_range(ev: dict) -> str:
    if ev.get("all_day"):
        return "All day"
    s = ev.get("start_min", 0)
    return f"{hhmm(s)}–{hhmm(s + ev.get('dur', 30))}"


def day_hours(events: list[dict], lo: int = GRID_START_H,
              hi: int = GRID_END_H) -> tuple[int, int]:
    """Grow the visible hours so no timed event falls off the grid."""
    for ev in events:
        if ev.get("all_day"):
            continue
        s = ev.get("start_min", 0)
        e = s + max(ev.get("dur", 30), 15)
        lo = min(lo, s // 60)
        hi = max(hi, min(24, -(-e // 60)))
    return lo, hi


def _font(family: str, px: int, weight=QFont.Weight.Normal) -> QFont:
    f = QFont(family)
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


def _lanes(events: list[dict]) -> list[tuple[dict, int, int]]:
    """(event, lane, lanes in its overlap cluster), so overlapping events
    share the column width instead of covering each other."""
    evs = sorted(events, key=lambda e: (e.get("start_min", 0),
                                        -e.get("dur", 30)))
    out: list[tuple[dict, int, int]] = []
    cluster: list[tuple[dict, int]] = []
    lane_end: list[int] = []
    cluster_end = -1

    def flush(items, n):
        for ev, lane in items:
            out.append((ev, lane, max(1, n)))

    for ev in evs:
        s = ev.get("start_min", 0)
        e = s + max(ev.get("dur", 30), 15)
        if cluster and s >= cluster_end:
            flush(cluster, len(lane_end))
            cluster, lane_end, cluster_end = [], [], -1
        for i, end in enumerate(lane_end):
            if end <= s:
                lane_end[i] = e
                lane = i
                break
        else:
            lane_end.append(e)
            lane = len(lane_end) - 1
        cluster.append((ev, lane))
        cluster_end = max(cluster_end, e)
    if cluster:
        flush(cluster, len(lane_end))
    return out


class _Painted(QWidget):
    """Base for custom-painted pieces: repaint on theme switches."""

    def __init__(self):
        super().__init__()
        T.manager().changed.connect(self._retheme)

    def _retheme(self, *_):
        self.update()


# ---- time grid pieces -------------------------------------------------------
class EventBlock(_Painted):
    """One timed event: a tinted rounded block, title plus time.
    Declined events (#125) are shown with greatly reduced opacity."""

    def __init__(self, ev: dict, on_click=None):
        super().__init__()
        self.ev = ev
        self._on_click = on_click
        if on_click is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        cal = f" · {ev['cal']}" if ev.get("cal") else ""
        tip = f"{ev.get('title', 'Untitled')}\n{time_range(ev)}{cal}"
        if ev.get("declined"):
            tip += "\n(You declined this)"
        self.setToolTip(tip)
        self.setAccessibleName(
            f"{ev.get('title', 'Untitled')}, {time_range(ev)}"
            + (" (declined)" if ev.get("declined") else ""))

    def mousePressEvent(self, e):
        if self._on_click and e.button() == Qt.MouseButton.LeftButton:
            fire_on_next_tick(lambda: self._on_click(self.ev))
        e.accept()

    def mouseDoubleClickEvent(self, e):
        e.accept()          # never start a new event from on top of one

    def paintEvent(self, e):
        t = T.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        declined = bool(self.ev.get("declined"))
        c = QColor(event_color(self.ev))
        # Declined events use the "muted" theme token colour so they recede
        # visually without inline colour hacks. (#125)
        if declined:
            c = t.color("muted")
        fill = QColor(c)
        fill.setAlpha((30 if t.dark else 20) if declined
                      else (70 if t.dark else 44))
        edge = QColor(c)
        edge.setAlpha(90 if declined else 170)
        p.setPen(QPen(edge, 1))
        p.setBrush(fill)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                          6, 6)
        w = self.width() - 14
        if w < 8:
            return
        tf = _font(T.FONT_SANS, 12, QFont.Weight.DemiBold)
        p.setFont(tf)
        p.setPen(t.color("muted" if declined else "fg"))
        fm = p.fontMetrics()
        p.drawText(QRectF(7, 3, w, fm.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(self.ev.get("title", "Untitled"),
                                 Qt.TextElideMode.ElideRight, w))
        if self.height() >= 36:
            mf = _font(T.FONT_MONO, 11)
            p.setFont(mf)
            p.setPen(t.color("muted"))
            fm2 = p.fontMetrics()
            p.drawText(QRectF(7, 4 + fm.height(), w, fm2.height()),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       fm2.elidedText(time_range(self.ev),
                                      Qt.TextElideMode.ElideRight, w))


class DayColumn(_Painted):
    """One day of time: hour rules painted, events placed by start and length.
    gutter > 0 paints hour labels in a left gutter (the Today schedule).
    on_slot(minute) fires on a double-click in empty space."""

    def __init__(self, events: list[dict], start_h: int = GRID_START_H,
                 end_h: int = GRID_END_H, hour_h: int = HOUR_H,
                 gutter: int = 0, on_event=None, on_slot=None,
                 left_rule: bool = True):
        super().__init__()
        self._start_h, self._end_h, self._hour_h = start_h, end_h, hour_h
        self._gutter, self._left_rule = gutter, left_rule
        self._on_slot = on_slot
        self._now: int | None = None
        self.setMinimumHeight((end_h - start_h) * hour_h + 2 * PAD_T)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        self._placed = []
        for ev, lane, n in _lanes([e for e in events if not e.get("all_day")]):
            b = EventBlock(ev, on_event)
            b.setParent(self)
            self._placed.append((b, lane, n))
        if on_slot is not None:
            self.setToolTip("Double-click an empty slot to add an event")

    def sizeHint(self):
        s = super().sizeHint()
        s.setHeight(self.minimumHeight())
        return s

    def set_now(self, minute: int | None) -> None:
        self._now = minute
        self.update()

    def _y(self, minute: float) -> float:
        return PAD_T + (minute - self._start_h * 60) / 60 * self._hour_h

    def resizeEvent(self, e):
        super().resizeEvent(e)
        x0 = self._gutter + 3
        span = max(self.width() - self._gutter - 6, 12)
        for b, lane, n in self._placed:
            ev = b.ev
            top = self._y(max(ev.get("start_min", 0), self._start_h * 60))
            h = max(ev.get("dur", 30) / 60 * self._hour_h, 22) - 2
            w = span / n
            b.setGeometry(int(x0 + lane * w), int(top), max(int(w) - 2, 8),
                          int(h))

    def mouseDoubleClickEvent(self, e):
        if self._on_slot is None or e.position().x() < self._gutter:
            return super().mouseDoubleClickEvent(e)
        minute = (e.position().y() - PAD_T) / self._hour_h * 60 \
            + self._start_h * 60
        minute = int(max(self._start_h * 60, min(minute, self._end_h * 60 - 30)))
        minute -= minute % 30
        self._on_slot(minute)

    def paintEvent(self, e):
        t = T.current()
        p = QPainter(self)
        p.setPen(QPen(t.color("border_soft"), 1))
        hours = self._end_h - self._start_h
        for i in range(hours + 1):
            y = int(self._y((self._start_h + i) * 60))
            p.drawLine(self._gutter, y, self.width(), y)
        if self._left_rule:
            p.drawLine(self._gutter, PAD_T, self._gutter,
                       int(self._y(self._end_h * 60)))
        if self._gutter:
            p.setFont(_font(T.FONT_MONO, 11))
            p.setPen(t.color("muted"))
            for i in range(hours + 1):
                y = self._y((self._start_h + i) * 60)
                p.drawText(QRectF(0, y - 8, self._gutter - 8, 16),
                           Qt.AlignmentFlag.AlignRight
                           | Qt.AlignmentFlag.AlignVCenter,
                           f"{self._start_h + i:02d}:00")
        if self._now is not None and \
                self._start_h * 60 <= self._now < self._end_h * 60:
            y = self._y(self._now)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(QPen(t.color("accent"), 2))
            p.drawLine(QRectF(self._gutter + 2, y, self.width() - self._gutter,
                              0).topLeft(),
                       QRectF(self.width(), y, 0, 0).topLeft())
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(t.color("accent"))
            p.drawEllipse(QRectF(self._gutter - 2, y - 4, 8, 8))


class TimeGutter(_Painted):
    """Right-aligned hour labels down the left edge of the week/day grid."""

    def __init__(self, width: int, start_h: int, end_h: int,
                 hour_h: int = HOUR_H):
        super().__init__()
        self._start_h, self._end_h, self._hour_h = start_h, end_h, hour_h
        self.setFixedWidth(width)
        self.setMinimumHeight((end_h - start_h) * hour_h + 2 * PAD_T)

    def paintEvent(self, e):
        t = T.current()
        p = QPainter(self)
        p.setFont(_font(T.FONT_MONO, 11))
        p.setPen(t.color("muted"))
        for i in range(self._end_h - self._start_h + 1):
            y = PAD_T + i * self._hour_h
            p.drawText(QRectF(0, y - 8, self.width() - 8, 16),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{self._start_h + i:02d}:00")


# ---- month grid -------------------------------------------------------------
class MonthCell(_Painted):
    """One day in the month grid: painted date number, up to three events,
    then "+N more". Click, Enter or Space opens the day."""

    MAX_SHOWN = 3

    def __init__(self, day: date, events: list[dict], in_month: bool,
                 is_today: bool, on_click=None):
        super().__init__()
        self.day, self._in_month, self._today = day, in_month, is_today
        self._on_click = on_click
        self.setMinimumHeight(96)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        n = len(events)
        self.setAccessibleName(
            f"{WD_FULL[day.weekday()]}, {MONTHS[day.month - 1]} {day.day}: "
            + (f"{n} event{'s' if n != 1 else ''}" if n else "nothing scheduled"))
        v = vbox(self, (8, 34, 6, 6), 2)
        shown = sorted(events, key=lambda e: (not e.get("all_day"),
                                              e.get("start_min", 0)))
        for ev in shown[:self.MAX_SHOWN]:
            row = hbox(s=6)
            declined = bool(ev.get("declined"))
            # Declined events use the "muted" token for the dot (#125).
            dot_fn = "muted" if declined else (lambda c=event_color(ev): c)
            row.addWidget(Dot(dot_fn, 6), 0,
                          Qt.AlignmentFlag.AlignVCenter)
            if not ev.get("all_day"):
                row.addWidget(Label(ev.get("start", ""), "meta"))
            row.addWidget(ElideLabel(ev.get("title", ""),
                                     "muted" if declined
                                     else ("small" if in_month else "muted")),
                          1)
            v.addLayout(row)
        more = n - min(n, self.MAX_SHOWN)
        if more > 0:
            v.addWidget(Label(f"+{more} more", "caption"))
        v.addStretch(1)
        tip = "\n".join(f"{time_range(e)}  {e.get('title', '')}"
                        + (" (declined)" if e.get("declined") else "")
                        for e in shown)
        if tip:
            self.setToolTip(tip)

    def event(self, ev):
        if ev.type() in (QEvent.Type.HoverEnter, QEvent.Type.HoverLeave):
            self.update()
        return super().event(ev)

    def _fire(self):
        if self._on_click is not None:
            fire_on_next_tick(lambda: self._on_click(self.day))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._fire()
        super().mousePressEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self._fire()
        else:
            super().keyPressEvent(e)

    def paintEvent(self, e):
        t = T.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        # Days outside the month are muted text on the same ground, not a
        # tint (review.md).
        p.fillRect(r, t.color("surface_warm" if self.underMouse() else "surface"))
        p.setPen(QPen(t.color("border_soft"), 1))
        p.drawLine(r.topRight(), r.bottomRight())
        p.drawLine(r.bottomLeft(), r.bottomRight())
        # date number
        num = QRectF(6, 6, 24, 24)
        p.setFont(_font(T.FONT_MONO, 12, QFont.Weight.DemiBold if self._today
                        else QFont.Weight.Normal))
        if self._today:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(t.color("accent"))
            p.drawEllipse(num)
            p.setPen(t.color("accent_on"))
        else:
            p.setPen(t.color("fg" if self._in_month else "muted"))
        p.drawText(num, Qt.AlignmentFlag.AlignCenter, str(self.day.day))
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(t.color("accent"), 2))
            p.drawRect(r.adjusted(1, 1, -1, -1))


class MonthGrid(QWidget):
    """6 x 7 month view; columns share the width evenly."""

    def __init__(self, anchor: date, events: list[dict], today: date,
                 on_day=None):
        super().__init__()
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        by_day: dict[str, list[dict]] = {}
        for ev in events:
            by_day.setdefault(ev["date"], []).append(ev)
        start = monday_of(anchor.replace(day=1))
        for w in range(6):
            for d in range(7):
                day = start + timedelta(days=w * 7 + d)
                grid.addWidget(MonthCell(
                    day, by_day.get(day.isoformat(), []),
                    in_month=day.month == anchor.month, is_today=day == today,
                    on_click=on_day), w, d)
        for c in range(7):
            grid.setColumnStretch(c, 1)
        for r in range(6):
            grid.setRowStretch(r, 1)


# ---- edit event overlay -----------------------------------------------------
class EventEditOverlay(_Overlay):
    """Edit an existing event: title, start/end, location and colour. Save
    hands the change to state.update_event; the daemon asks you to confirm
    before anything on Google Calendar changes."""

    CARD_W = 520

    def __init__(self, window, state, on_done=None):
        super().__init__(window)
        self.state = state
        self._on_done = on_done
        self.ev: dict = {}
        self._timed = False
        v = vbox(self.card, (0, 0, 0, 0), 0)
        head, self.heading = _dialog_head(
            "Edit event", "Google Calendar. You confirm before it changes.",
            close=self.close_overlay)
        v.addLayout(head)
        body = vbox(m=(T.S6, 0, T.S6, T.S5), s=T.S4)
        self.title_f = TextField("Title", placeholder="Event title")
        body.addWidget(self.title_f)
        self.times = QWidget()
        tr = hbox(self.times, (0, 0, 0, 0), T.S3)
        self.start_f = TextField("Starts", "Like 14:00, 9:30 or 2pm", "14:00")
        self.end_f = TextField("Ends", "", "15:00")
        tr.addWidget(self.start_f, 1)
        tr.addWidget(self.end_f, 1)
        body.addWidget(self.times)
        self.loc_f = TextField("Location", "Leave blank to keep it as it is.")
        body.addWidget(self.loc_f)
        body.addWidget(Label("Colour", "field-label"))
        self.color = QComboBox()
        self.color.setAccessibleName("Colour")
        self.color.addItem("Keep the current colour", "")
        for cid, name in GCAL_COLOR_NAMES.items():
            self.color.addItem(name, cid)
        body.addWidget(self.color)
        v.addLayout(body, 1)
        foot, fl = _dialog_foot()
        fl.addStretch(1)
        fl.addWidget(Button("Cancel", "secondary", on_click=self.close_overlay))
        fl.addWidget(Button("Review changes", "primary", on_click=self._save))
        v.addWidget(foot)
        for f in (self.title_f, self.start_f, self.end_f, self.loc_f):
            f.input.returnPressed.connect(self._save)

    def open(self, ev: dict):
        self.ev = ev
        for f in (self.title_f, self.start_f, self.end_f):
            f.clear_error()
        self.title_f.set_text(ev.get("title", ""))
        _day, start, dur = _whole(ev)
        # A timed event a day or longer can't be expressed as two clock times
        # on one date; editing them would cut it short. Title, place and
        # colour still edit; times are left alone.
        timed = not ev.get("all_day") and dur < 24 * 60
        self._timed = timed
        self.times.setVisible(timed)
        if timed:
            self.start_f.set_text(hhmm(start))
            self.end_f.set_text(_end_hhmm(ev))
        self.loc_f.set_text("")
        self.color.setCurrentIndex(0)
        self.pop()
        self.title_f.input.setFocus()

    def _iso(self, value: str, next_day: bool = False) -> str:
        day = date.fromisoformat(_whole(self.ev)[0])
        if value == "24:00":
            value, next_day = "00:00", True
        if next_day:
            day += timedelta(days=1)
        h, _, m = value.partition(":")
        return (f"{day.isoformat()}T{int(h):02d}:{m}:00"
                f"{tz_offset(day, int(h), int(m))}")

    def _save(self):
        if not self.isVisible():
            return
        ev = self.ev
        changes: dict = {"all_day": bool(ev.get("all_day"))}
        title = self.title_f.text().strip()
        if not title:
            self.title_f.set_error("Give the event a title.")
            self.title_f.input.setFocus()
            return
        if title != ev.get("title"):
            changes["title"] = title
        if self._timed:
            # Same reader as the New-event form: "9:30", "2pm", "14:00".
            s_min = parse_clock(self.start_f.text())
            if s_min is None:
                self.start_f.set_error(EventOverlay.BAD_TIME)
                self.start_f.input.setFocus()
                return
            e_txt = self.end_f.text().strip()
            e_min = parse_clock(e_txt, end=True) if e_txt else s_min
            if e_min is None:
                self.end_f.set_error(EventOverlay.BAD_TIME)
                self.end_f.input.setFocus()
                return
            s, e = hhmm(s_min), hhmm(e_min)
            orig_s = hhmm(_whole(ev)[1])
            if (s, e) != (orig_s, _end_hhmm(ev)):
                changes["start"] = self._iso(s)
                # An end at or before the start runs past midnight (L5).
                changes["end"] = self._iso(
                    e, next_day=e != "24:00" and e != s
                    and _minutes(e) <= _minutes(s))
        loc = self.loc_f.text().strip()
        if loc:
            changes["location"] = loc
        cid = self.color.currentData()
        if cid:
            changes["color_id"] = cid
        if len(changes) == 1:
            self.close_overlay()
            if self._on_done:
                self._on_done({"message": "Nothing to change."})
            return
        self.close_overlay()
        self.state.update_event(ev.get("id"), ev.get("calendar_id") or "primary",
                                changes, self._on_done)


# ---- screen -----------------------------------------------------------------
class CalendarScreen(QWidget):
    VIEWS = (("month", "Month"), ("week", "Week"), ("day", "Day"))

    def __init__(self, window):
        super().__init__()
        self.win = window
        self.state = window.state
        self.view = "month"
        self.anchor = date.today()
        self.today = date.today()
        self._events: list[dict] = []
        self._connected = True
        self._loaded = False
        self._load_error = False      # the last load failed and nothing is shown
        self._req = 0
        self._narrow = False
        self._editor: EventEditOverlay | None = None
        self._splitter: QSplitter | None = None
        # Sync window and status — forwarded from the daemon response (#116/#117).
        self._sync_window: tuple[str, str] | None = None
        self._last_sync: str | None = None
        self._sync_failed = False
        self._sync_paused = False
        # Day columns showing today; the minute timer moves their now-line.
        self._now_cols: list[DayColumn] = []
        self._now_timer = QTimer(self)
        self._now_timer.setInterval(60_000)
        self._now_timer.timeout.connect(self._tick_now)

        root = vbox(self, (T.S8, T.S6, T.S8, T.S4), T.S4)
        self.header = ScreenHeader(
            "", "Your Google Calendar. Lumen asks before it changes anything.",
            "Calendar")
        self.seg = SegmentedControl(self.VIEWS, "month", "Calendar view")
        self.seg.changed.connect(self._set_view)
        self.header.add_action(self.seg)
        self.refresh_btn = IconButton("refresh", "Sync with Google Calendar",
                                      on_click=self.force_refresh)
        self.header.add_action(self.refresh_btn)
        self.header.add_action(Button("New event", "primary", icon="plus",
                                      on_click=self._new_event))
        root.addWidget(self.header)

        nav = hbox(s=T.S2)
        self.prev_btn = IconButton("chevron-left", "Previous month",
                                   on_click=lambda: self._shift(-1))
        nav.addWidget(self.prev_btn)
        nav.addWidget(Button("Today", "secondary", size="sm",
                             on_click=self._go_today))
        self.next_btn = IconButton("chevron-right", "Next month",
                                   on_click=lambda: self._shift(1))
        nav.addWidget(self.next_btn)
        nav.addSpacing(T.S2)
        self.count_label = Label("", "muted")
        nav.addWidget(self.count_label)
        nav.addStretch(1)
        self.legend = QWidget()
        self.legend_lay = hbox(self.legend, (0, 0, 0, 0), T.S4)
        nav.addWidget(self.legend)
        root.addLayout(nav)

        self.body = QWidget()
        self.body_lay = vbox(self.body, (0, 0, 0, 0), 0)
        root.addWidget(self.body, 1)

        # Creating/editing/deleting finishes behind the confirm overlay; once
        # it closes, re-read the window so the change shows up here.
        confirm = getattr(window, "confirm", None)
        if confirm is not None:
            confirm.installEventFilter(self)
        self.rebuild()

    # ---- hooks --------------------------------------------------------------
    def on_shown(self, date=None, view=None, new=False):  # noqa: A002
        moved = self._roll_day()
        if view in ("month", "week", "day") and view != self.view:
            self.view = view
            self.seg.set_value(view)
            moved = True
        if date:
            try:
                d = date if hasattr(date, "isoformat") and not isinstance(
                    date, str) else datetime.fromisoformat(str(date)).date()
                self.anchor = d
                moved = True
            except ValueError:
                pass
        if moved:
            self.rebuild()
        # Showing the screen is when you expect current data (#59); debounced
        # to one Google sync a minute in AppState.
        self.sync()
        if new:
            fire_on_next_tick(self._new_event)

    def ask_context(self) -> str:
        frm, to = self._range()
        lines = [f"Calendar screen, {self.view} view: {self._title()}."]
        if not self._connected:
            lines.append("Google Calendar is not connected.")
            return "\n".join(lines)
        # If the current view is outside the sync window, say so clearly so
        # the model doesn't make free-time claims about unsynced dates (#116).
        if self._outside_window(frm, to):
            win = self._sync_window
            if win:
                lines.append(
                    f"This date range is outside Lumen's synced window "
                    f"({win[0]} to {win[1]}). Event data is not available.")
            else:
                lines.append("This date range may be outside the synced window.")
            return "\n".join(lines)
        evs = sorted(
            (e for e in self._events
             if frm.isoformat() <= (e.get("date", "")) <= to.isoformat()),
            key=lambda e: ((e.get("date", "")),
                           e.get("start_min", 0)))
        if not evs:
            lines.append("No events in this range.")
        for e in evs[:60]:
            day_iso = e.get("date", "")
            try:
                d = date.fromisoformat(day_iso)
            except ValueError:
                continue
            cal = f" ({e['cal']})" if e.get("cal") else ""
            declined = " [declined]" if e.get("declined") else ""
            lines.append(f"- {WEEKDAYS[d.weekday()]} {MON3[d.month - 1]} "
                         f"{d.day} {time_range(e)}: "
                         f"{e.get('title', '')}{cal}{declined}")
        return "\n".join(lines)

    def apply_theme(self):
        for w in self.findChildren(_Painted):
            w.update()

    def nav_token(self):
        return (self.view, self.anchor)

    def nav_restore(self, token):
        # Replaying history: no nav_location_changed here.
        self.view, self.anchor = token
        self.seg.set_value(self.view)
        self.rebuild()
        self.refresh()

    # ---- data -----------------------------------------------------------------
    def _range(self) -> tuple[date, date]:
        if self.view == "month":
            start = monday_of(self.anchor.replace(day=1))
            return start, start + timedelta(days=41)
        if self.view == "week":
            start = monday_of(self.anchor)
            return start, start + timedelta(days=6)
        return self.anchor, self.anchor

    def _cb(self):
        self._req += 1
        mine = self._req

        def done(result):
            self.refresh_btn.setEnabled(True)
            if mine != self._req:
                return                  # a newer request is on its way
            self._loaded_result(result or {})
        return done

    def refresh(self):
        """Re-read the daemon's cache: instant, what navigation uses."""
        frm, to = self._range()
        self.state.fetch_calendar(frm.isoformat(), to.isoformat(), self._cb())

    def sync(self):
        frm, to = self._range()
        self.state.sync_calendar(frm.isoformat(), to.isoformat(), self._cb())

    def force_refresh(self):
        """The sync button always does a real Google sync."""
        frm, to = self._range()
        self.refresh_btn.setEnabled(False)
        self.state.refresh_calendar(frm.isoformat(), to.isoformat(), self._cb())

    def _loaded_result(self, result: dict):
        self._loaded = True
        # Sync status (#117) — update regardless of error so the header
        # reflects the most recent attempt.
        win = result.get("window")
        if win and len(win) == 2:
            self._sync_window = (win[0], win[1])
        ls = result.get("last_sync")
        if ls is not None:
            self._last_sync = ls
        # Only a refresh says how a Google pull went; a cache read (None)
        # keeps the last word, so a failure doesn't vanish on navigation.
        if result.get("sync_failed") is not None:
            self._sync_failed = bool(result["sync_failed"])
        if result.get("sync_paused") is not None:
            self._sync_paused = bool(result["sync_paused"])

        if result.get("error"):
            if not self._events:
                self._load_error = True
                self.rebuild()
            else:
                # Show the error in the header too (failed manual ↻).
                self._sync_failed = True
                self.win.show_toast(
                    self._sync_status_text() or
                    "Couldn't refresh the calendar. Showing what Lumen already had.")
                self._sync_chrome()   # update the status label without full rebuild
            return
        self._load_error = False
        self._events = expand_events(result.get("events", []))
        self._connected = result.get("connected", True)
        self.rebuild()

    def _write_done(self, result):
        r = result or {}
        msg = r.get("message")
        if msg:
            self.win.show_toast(msg)
        self.refresh()

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Hide and obj is getattr(self.win, "confirm",
                                                            None):
            QTimer.singleShot(1500, self._refresh_if_visible)
        return False

    def _refresh_if_visible(self):
        if self.isVisible():
            self.refresh()

    # ---- navigation -------------------------------------------------------------
    def _navigated(self):
        self.rebuild()
        self.refresh()
        self.state.nav_location_changed.emit()

    def _set_view(self, key: str):
        self.view = key
        self._navigated()

    def _go_today(self):
        self.anchor = date.today()
        self._navigated()

    def _shift(self, sign: int):
        if self.view == "month":
            first = self.anchor.replace(day=1)
            self.anchor = ((first - timedelta(days=1)).replace(day=1) if sign < 0
                           else (first + timedelta(days=32)).replace(day=1))
        elif self.view == "week":
            self.anchor += timedelta(days=7 * sign)
        else:
            self.anchor += timedelta(days=sign)
        self._navigated()

    def _open_day(self, d: date):
        self.anchor = d
        self.view = "day"
        self.seg.set_value("day")
        self._navigated()

    def _new_event(self, start_min: int | None = None, day: date | None = None):
        payload = {"date": (day or self.anchor).isoformat()}
        if start_min is not None:
            payload["start"] = hhmm(start_min)
            payload["end"] = hhmm(start_min + 60)
        self.win.open_event(payload)

    def _edit_event(self, ev: dict):
        if self._editor is None:
            self._editor = EventEditOverlay(self.win, self.state, self._write_done)
        self._editor.open(ev)

    def _delete_event(self, ev: dict):
        # External write: the daemon shows the confirm before touching Google.
        self.state.delete_event(ev["id"], ev.get("calendar_id") or "primary",
                                self._write_done)

    # ---- now line -----------------------------------------------------------------
    # One wake-up a minute, and only while the screen is on show (power budget).
    def showEvent(self, ev):
        super().showEvent(ev)
        self._now_timer.start()
        self._tick_now()

    def hideEvent(self, ev):
        self._now_timer.stop()
        super().hideEvent(ev)

    def _roll_day(self) -> bool:
        """A new day since `today` was set (#115): a view that was on the old
        today moves to the new one — one you navigated to stays. The only
        place `today` changes, so the two can't drift apart. True when the
        view moved: its events are another range's, so the caller fetches."""
        real = datetime.now().date()
        if real == self.today:
            return False
        moved = self.anchor == self.today
        if moved:
            self.anchor = real
        self.today = real
        return moved

    def _tick_now(self):
        if datetime.now().date() != self.today:
            if self._roll_day():
                self.sync()             # its answer rebuilds
            self.rebuild()
            return
        m = self._now_minute()
        for col in list(self._now_cols):
            try:
                col.set_now(m)
            except RuntimeError:        # the column was rebuilt away
                self._now_cols.remove(col)

    # ---- layout -------------------------------------------------------------------
    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        narrow = self.width() < NARROW_W
        if narrow != self._narrow:
            self._narrow = narrow
            self._apply_narrow()

    def _apply_narrow(self):
        self.legend.setVisible(not self._narrow)
        if self._splitter is not None:
            self._splitter.setOrientation(
                Qt.Orientation.Vertical if self._narrow
                else Qt.Orientation.Horizontal)

    # ---- build ----------------------------------------------------------------------
    def _title(self) -> str:
        a = self.anchor
        if self.view == "month":
            return f"{MONTHS[a.month - 1]} {a.year}"
        if self.view == "week":
            mon = monday_of(a)
            sun = mon + timedelta(days=6)
            if mon.month == sun.month:
                return f"{MON3[mon.month - 1]} {mon.day} – {sun.day}, {sun.year}"
            return (f"{MON3[mon.month - 1]} {mon.day} – "
                    f"{MON3[sun.month - 1]} {sun.day}, {sun.year}")
        return f"{WD_FULL[a.weekday()]}, {MON3[a.month - 1]} {a.day}"

    def _in_range(self) -> list[dict]:
        frm, to = self._range()
        lo, hi = frm.isoformat(), to.isoformat()
        if self.view == "month":
            first = self.anchor.replace(day=1)
            nxt = (first + timedelta(days=32)).replace(day=1)
            lo, hi = first.isoformat(), (nxt - timedelta(days=1)).isoformat()
        return [e for e in self._events
                if lo <= (e.get("date", "")) <= hi]

    def _outside_window(self, frm: date, to: date) -> bool:
        """True when the requested range is entirely outside the sync window."""
        if not self._sync_window:
            return False
        w_from, w_to = self._sync_window
        return to.isoformat() < w_from or frm.isoformat() > w_to

    def _sync_status_text(self) -> str:
        """One-line status for the calendar header (shown on show/refresh)."""
        if self._sync_paused:
            return "Sync paused"
        if self._sync_failed:
            if self._last_sync:
                try:
                    dt = datetime.fromisoformat(self._last_sync).astimezone()
                    t = dt.strftime("%-H:%M")
                except (ValueError, TypeError):
                    t = self._last_sync
                return f"Couldn't reach Google Calendar — showing events as of {t}"
            return "Couldn't reach Google Calendar"
        if self._last_sync:
            try:
                delta = (datetime.now().astimezone()
                         - datetime.fromisoformat(self._last_sync).astimezone())
                mins = int(delta.total_seconds() // 60)
                if mins < 1:
                    return "Updated just now"
                if mins < 60:
                    return f"Updated {mins} min ago"
                return f"Updated {mins // 60} h ago"
            except (ValueError, TypeError):
                pass
        return ""

    def _sync_chrome(self):
        self.header.set_title(self._title())
        unit = {"month": "month", "week": "week", "day": "day"}[self.view]
        self.prev_btn.set_tooltip(f"Previous {unit}")
        self.next_btn.set_tooltip(f"Next {unit}")
        if not self._loaded or not self._connected:
            self.count_label.setText("")
        else:
            frm, to = self._range()
            if self._outside_window(frm, to):
                self.count_label.setText("")
            else:
                # Events, not day pieces: a weekend trip is one (#113).
                # Sample events have no id; each counts once.
                n = len({(e.get("calendar_id"), e.get("id")) if e.get("id")
                         else idx for idx, e in enumerate(self._in_range())})
                this = {"month": "this month", "week": "this week",
                        "day": "on this day"}[self.view]
                self.count_label.setText(
                    f"{n} event{'s' if n != 1 else ''} {this}" if n
                    else f"Nothing scheduled {this}")
        # Sync-status label under the title (#117) — recomputed on show/refresh,
        # never on a polling timer.
        status = self._sync_status_text() if self._loaded else ""
        self.header.set_subtitle(status if status
                                 else "Your Google Calendar. Lumen asks "
                                      "before it changes anything.")
        clear_layout(self.legend_lay)
        cals: dict[str, str] = {}
        for e in self._in_range():
            if e.get("cal") and e["cal"] not in cals:
                cals[e["cal"]] = calendar_color(e)
        for name, color in list(cals.items())[:6]:
            item = QWidget()
            h = hbox(item, (0, 0, 0, 0), 6)
            h.addWidget(Dot(lambda c=color: c, 8), 0,
                        Qt.AlignmentFlag.AlignVCenter)
            h.addWidget(Label(name, "small"))
            self.legend_lay.addWidget(item)
        self.legend.setVisible(bool(cals) and not self._narrow)

    def rebuild(self):
        if self._roll_day():
            self.sync()                 # the view moved to a new day (#115)
        self._splitter = None
        self._now_cols = []
        clear_layout(self.body_lay)
        self._sync_chrome()
        if not self._loaded:
            box = QWidget()
            v = vbox(box, (0, T.S4, 0, 0), T.S3)
            for _ in range(5):
                v.addWidget(SkeletonRow(2, height=64))
            v.addStretch(1)
            self.body_lay.addWidget(box)
            return
        if self._load_error:
            self.body_lay.addWidget(EmptyState(
                "alert", "Couldn't load your calendar",
                "Lumen couldn't reach Google Calendar, or its background "
                "service isn't running.", "Try again", self.force_refresh))
            return
        if not self._connected:
            self.body_lay.addWidget(EmptyState(
                "calendar", "Google Calendar isn't connected",
                "Lumen can't show events until your Google account is "
                "connected. You can connect it in Settings.",
                "Open Settings", lambda: self.win.switch_to("settings")))
            return
        # Out-of-sync-window empty state (#116) — only for day/week view where
        # the empty state would mislead. Month shows what's cached with context.
        if self.view in ("day", "week"):
            frm, to = self._range()
            if self._outside_window(frm, to):
                win = self._sync_window
                sub = ("Lumen keeps your calendar "
                       f"from {_short_day(win[0])} to {_short_day(win[1])}. "
                       "This period is outside that range." if win
                       else "This period is outside what Lumen keeps.")
                self.body_lay.addWidget(EmptyState(
                    "calendar", "Outside what Lumen keeps", sub,
                    "Go to today", self._go_today))
                return
        if self.view == "month":
            self.body_lay.addWidget(self._month())
        elif self.view == "week":
            self.body_lay.addWidget(self._week())
        else:
            self.body_lay.addWidget(self._day())

    def _now_minute(self) -> int:
        now = datetime.now()
        return now.hour * 60 + now.minute

    def _month(self) -> QWidget:
        w = QWidget()
        v = vbox(w, (0, 0, 0, 0), 0)
        head = hbox(s=0)
        for d in WEEKDAYS:
            lab = Label(d, "caption")
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lab.setContentsMargins(0, T.S2, 0, T.S2)
            head.addWidget(lab, 1)
        v.addLayout(head)
        grid = MonthGrid(self.anchor, self._events, self.today,
                         on_day=self._open_day)
        v.addWidget(ScrollArea(grid), 1)
        return w

    def _all_day_strip(self, days: list[date], gutter: int) -> QWidget | None:
        by_day: dict[str, list[dict]] = {}
        for e in self._events:
            if e.get("all_day"):
                by_day.setdefault(e["date"], []).append(e)
        if not any(by_day.get(d.isoformat()) for d in days):
            return None
        strip = QWidget()
        h = hbox(strip, (0, T.S1, 0, T.S1), 0)
        cap = Label("All day", "caption")
        cap.setFixedWidth(gutter)
        h.addWidget(cap, 0, Qt.AlignmentFlag.AlignTop)
        for d in days:
            col = vbox(m=(3, 0, 3, 0), s=2)
            for e in by_day.get(d.isoformat(), []):
                row = hbox(s=6)
                row.addWidget(Dot(lambda c=event_color(e): c, 6), 0,
                              Qt.AlignmentFlag.AlignVCenter)
                lab = ElideLabel(e.get("title", ""), "small")
                row.addWidget(lab, 1)
                col.addLayout(row)
            col.addStretch(1)
            h.addLayout(col, 1)
        return strip

    def _week(self) -> QWidget:
        w = QWidget()
        v = vbox(w, (0, 0, 0, 0), 0)
        mon = monday_of(self.anchor)
        days = [mon + timedelta(days=i) for i in range(7)]
        gutter = 56

        head = hbox(s=0)
        spacer = QWidget()
        spacer.setFixedWidth(gutter)
        head.addWidget(spacer)
        for i, d in enumerate(days):
            cell = ClickRow(lambda dd=d: self._open_day(dd), selected=d == self.today,
                            accessible_name=f"Open {WD_FULL[d.weekday()]} "
                                            f"{MONTHS[d.month - 1]} {d.day}")
            cv = vbox(cell, (0, T.S2, 0, T.S2), 0)
            dow = Label(WEEKDAYS[i], "caption")
            dow.setAlignment(Qt.AlignmentFlag.AlignCenter)
            num = Label(str(d.day), "h3")
            num.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cv.addWidget(dow)
            cv.addWidget(num)
            head.addWidget(cell, 1)
        v.addLayout(head)
        strip = self._all_day_strip(days, gutter)
        if strip is not None:
            v.addWidget(strip)

        lo_iso, hi_iso = days[0].isoformat(), days[-1].isoformat()
        week_events = [e for e in self._events
                       if lo_iso <= (e.get("date", "")) <= hi_iso]
        start_h, end_h = day_hours(week_events)
        grid = QWidget()
        g = hbox(grid, (0, 0, 0, 0), 0)
        g.addWidget(TimeGutter(gutter, start_h, end_h))
        by_day: dict[str, list[dict]] = {}
        for e in week_events:
            by_day.setdefault(e["date"], []).append(e)
        for d in days:
            col = DayColumn(by_day.get(d.isoformat(), []), start_h, end_h,
                            on_event=lambda _e, dd=d: self._open_day(dd),
                            on_slot=lambda m, dd=d: self._new_event(m, dd))
            if d == self.today:
                col.set_now(self._now_minute())
                self._now_cols.append(col)
            g.addWidget(col, 1)
        v.addWidget(ScrollArea(grid), 1)
        return w

    def _day(self) -> QWidget:
        anchor_iso = self.anchor.isoformat()
        todays = [e for e in self._events
                  if e.get("date") == anchor_iso]
        start_h, end_h = day_hours(todays)

        grid = QWidget()
        gv = vbox(grid, (0, 0, 0, 0), 0)
        strip = self._all_day_strip([self.anchor], 64)
        if strip is not None:
            gv.addWidget(strip)
        col = DayColumn(todays, start_h, end_h, gutter=64,
                        on_event=self._edit_if_editable,
                        on_slot=lambda m: self._new_event(m, self.anchor))
        if self.anchor == self.today:
            col.set_now(self._now_minute())
            self._now_cols.append(col)
        gv.addWidget(col)
        gv.addStretch(1)
        grid_scroll = ScrollArea(grid)

        agenda = ScrollArea(m=(T.S5, T.S2, T.S2, T.S4), s=T.S1)
        agenda.setMinimumWidth(240)
        agenda.lay.addWidget(Eyebrow("Agenda"))
        agenda.lay.addSpacing(T.S2)
        if todays:
            for ev in sorted(todays, key=lambda e: (not e.get("all_day"),
                                                    e.get("start_min", 0))):
                agenda.lay.addWidget(self._agenda_row(ev))
        else:
            empty = EmptyState(
                "calendar", "Nothing scheduled",
                "There are no events on this day.",
                "Add an event", lambda: self._new_event(day=self.anchor))
            empty.layout().setContentsMargins(0, T.S6, 0, T.S6)
            agenda.lay.addWidget(empty)
        agenda.lay.addStretch(1)

        sp = QSplitter(Qt.Orientation.Vertical if self._narrow
                       else Qt.Orientation.Horizontal)
        sp.setChildrenCollapsible(False)
        sp.addWidget(grid_scroll)
        sp.addWidget(agenda)
        sp.setStretchFactor(0, 3)
        sp.setStretchFactor(1, 1)
        sp.setSizes([700, 300])
        self._splitter = sp
        return sp

    def _edit_if_editable(self, ev: dict):
        if ev.get("id"):
            self._edit_event(ev)

    def _agenda_row(self, ev: dict) -> QWidget:
        row = QWidget()
        row.setMinimumHeight(T.ROW_H)
        h = hbox(row, (0, T.S2, 0, T.S2), T.S3)
        declined = bool(ev.get("declined"))
        # Declined events use the "muted" theme token for the dot (#125).
        dot_color = "muted" if declined else (lambda c=event_color(ev): c)
        h.addWidget(Dot(dot_color, 8), 0, Qt.AlignmentFlag.AlignTop)
        col = vbox(s=2)
        # Title in muted role when declined; "body" otherwise.
        title_role = "muted" if declined else "body"
        col.addWidget(Label(ev.get("title", "Untitled"), title_role, wrap=True))
        meta = hbox(s=T.S2)
        if ev.get("all_day"):
            meta.addWidget(Badge("neutral", "All day"))
        else:
            meta.addWidget(Label(time_range(ev), "mono"))
        if ev.get("cal"):
            meta.addWidget(Label(ev["cal"], "muted"))
        if declined:
            meta.addWidget(Badge("neutral", "Declined"))
        meta.addStretch(1)
        col.addLayout(meta)
        h.addLayout(col, 1)
        # Edit and delete live only in the agenda, never on grid blocks where
        # a stray click on a dense week would fire them (ui_v3).
        if ev.get("id"):
            _t = ev.get("title", "")
            h.addWidget(IconButton("edit", f"Edit “{_t}”",
                                   size=32, icon_size=16,
                                   on_click=lambda e=ev: self._edit_event(e)),
                        0, Qt.AlignmentFlag.AlignTop)
            h.addWidget(IconButton("trash", f"Delete “{_t}”",
                                   size=32, icon_size=16,
                                   on_click=lambda e=ev: self._delete_event(e)),
                        0, Qt.AlignmentFlag.AlignTop)
        return row
