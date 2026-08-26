"""Canvas tab: the U-of-U Canvas login in an embedded browser, plus the synced
assignments and announcements.

Two modes share the screen through a QStackedWidget: the *content* view (synced
assignments + announcements, always scrollable so a full term never crushes the
rows) and the *login* view (a lazy QWebEngineView built only on Connect, so
constructing the window stays headless-safe and Chromium runs only during
login — zero idle cost on the iGPU laptop). On a successful login the session
cookies are forwarded to the daemon (canvas.set_session); the password, if the
user asks to remember it, is captured from the CAS form and saved to the OS
keyring via canvas_creds."""

import html
import json
import logging
import os
import re
from datetime import date, datetime, timezone

from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtWidgets import (
    QCheckBox, QFrame, QStackedWidget, QVBoxLayout, QWidget,
)

from .. import canvas_creds
from .. import canvas_login as cl
from .. import theme as T
from ..components import section_head
from ..widgets import (
    AccentBar, Chip, ClickLabel, ClickRow, ElideLabel, Dot, IconButton, Switch,
    button, clear_layout, empty_state, eyebrow, hbox, hline, label, qcolor,
    scroll, vbox,
)

log = logging.getLogger("lumen.ui.canvas")

# Overdue accent — a readable brick red that sits in the app's warm palette.
_OVERDUE = "#b3402f"

# The U-of-U Canvas host. Browsing goes to the dashboard; connecting goes to the
# login page (which autofills saved credentials). One host for both (#28).
_CANVAS_BASE = "https://utah.instructure.com"
_CANVAS_HOST = cl.cookie_host(_CANVAS_BASE)
# Opt-in autofill diagnostics; see canvas_login.py's module docstring.
DEBUG_AUTOFILL = os.environ.get("LUMEN_CANVAS_DEBUG") == "1"


def _tint(hex_color: str, alpha: float) -> str:
    """An #rrggbbaa (alpha-last) tint of a course colour for the course-code
    chip — enough to read the course identity at a glance without the chip
    shouting (#28 audit: 3px rails did no work). qcolor() reads alpha-last hex;
    a CSS rgba() string would parse to black."""
    return hex_color + f"{max(0, min(255, round(alpha * 255))):02x}"


def _due_meta(due_at: str, done: bool = False) -> tuple[str, str, str]:
    """(text, fg, border) for an assignment's due chip, coloured by urgency:
    overdue = red, due within 3 days = amber, later = muted. Turns a bare ISO
    date into a human 'in 2 days' / 'overdue' the way a planner would. A `done`
    item (submitted or graded) never reads as overdue — its past due date is just
    quiet context, not an alarm (audit)."""
    iso = (due_at or "")[:10]
    if not iso:
        return "no due date", T.TEXT_FAINTER, T.BORDER_FIELD
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return iso, T.TEXT_MUTED, T.BORDER_FIELD
    days = (d - date.today()).days
    when = d.strftime("%b %-d")
    if done:
        return when, T.TEXT_MUTED, T.BORDER_FIELD
    if days < 0:
        return f"overdue · {when}", _OVERDUE, T.BORDER_DANGER
    if days == 0:
        return "due today", T.WARN, T.BORDER_DUE
    if days <= 3:
        return f"in {days}d · {when}", T.WARN, T.BORDER_DUE
    return when, T.TEXT_MUTED, T.BORDER_FIELD


def _pretty_when(value) -> str:
    """A proposal's stored start — a bare date, or local ISO — rendered for a
    review card."""
    text = str(value or "")
    if not text:
        return ""
    try:
        if "T" in text:
            dt = datetime.fromisoformat(text)
            return dt.strftime("%a, %b %-d at %-I:%M %p")
        return date.fromisoformat(text).strftime("%a, %b %-d")
    except (ValueError, TypeError):
        return text


def _due_key(a: dict):
    """Sort assignments soonest-first; those with no due date sink to the end."""
    iso = (a.get("due_at") or "")[:10]
    try:
        return (0, date.fromisoformat(iso))
    except ValueError:
        return (1, date.max)


# Time-section buckets for the assignment timeline (#28 rework). Rolling windows
# relative to today — a planner reads "what's next", not "which Monday". The
# labels name the actual windows so a bucket never disagrees with the reader's
# sense of "this week" (audit #11). Ordering matches _due_key.
_SECTIONS = ["Overdue", "Next 7 days", "In 1–2 weeks", "Later", "No due date"]


def _time_bucket(due_at: str) -> int:
    """Index into _SECTIONS for one assignment's due date."""
    iso = (due_at or "")[:10]
    try:
        days = (date.fromisoformat(iso) - date.today()).days
    except ValueError:
        return 4                       # no / unparseable due date
    if days < 0:
        return 0
    if days <= 6:
        return 1
    if days <= 13:
        return 2
    return 3


def _num(points) -> str:
    """'100' / '2.5' / '' — drop a trailing .0 the way a syllabus would."""
    if points in (None, ""):
        return ""
    try:
        f = float(points)
    except (TypeError, ValueError):
        return ""
    n = int(f) if f.is_integer() else f
    return f"{n}"


def _parse_dt(iso: str | None):
    """An ISO timestamp as an aware datetime, or None. A *naive* stamp is read
    as local time — the daemon writes last_sync with a bare datetime.now(),
    and reading it as UTC put every fresh sync hours in the past."""
    raw = (iso or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.astimezone()


def _age_seconds(iso: str | None) -> float | None:
    dt = _parse_dt(iso)
    return None if dt is None else (datetime.now(timezone.utc) - dt).total_seconds()


def _ago(iso: str) -> str:
    """A relative 'just now / 3h ago / 2d ago', falling back to the date once an
    announcement is older than a week. Keeps the feed scannable at a glance."""
    raw = (iso or "").strip()
    if not raw:
        return ""
    dt = _parse_dt(raw)
    if dt is None:
        return raw[:10]
    secs = (datetime.now(timezone.utc) - dt).total_seconds()
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    if secs < 604800:
        return f"{int(secs // 86400)}d ago"
    return dt.strftime("%b %-d")


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(msg: str, limit: int = 180) -> str:
    """Announcement bodies are HTML; the feed wants a couple of plain-text lines.
    Strip tags, unescape entities, collapse whitespace, and cap the length."""
    text = _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", msg or ""))).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


# How old the mirror may be before opening the Canvas tab pulls again. Well
# under the poll interval, so a tab visit is always worth something, but long
# enough that flipping between tabs doesn't hammer Canvas.
_STALE_MINUTES = 5


def _stale(iso: str | None) -> bool:
    """True when the last sync is missing, unreadable, or older than
    _STALE_MINUTES — i.e. opening the tab should pull."""
    secs = _age_seconds(iso)
    return secs is None or secs > _STALE_MINUTES * 60


class _StatusPill(QWidget):
    """'● last sync 2m ago' — the status dot + mono caption in the header,
    updatable in place (synced_pill builds a fixed one)."""

    def __init__(self):
        super().__init__()
        row = hbox(self, (0, 0, 0, 0), 7)
        self._dot = Dot(6, T.TEXT_GHOST)
        self._lbl = label("Not connected", 10, T.TEXT_MUTED, mono=True, ls=1)
        row.addWidget(self._dot)
        row.addWidget(self._lbl)

    def set_status(self, text: str, text_color: str, dot_color: str):
        self._dot.set_color(dot_color)
        pal = self._lbl.palette()
        pal.setColor(self._lbl.foregroundRole(), qcolor(text_color))
        self._lbl.setPalette(pal)
        self._lbl.setText(text)


class CanvasScreen(QWidget):
    # QStackedWidget page indices: content, the in-app browser, the Manage panel.
    _CONTENT_IDX = 0
    _BROWSER_IDX = 1
    _MANAGE_IDX = 2
    _REVIEW_IDX = 3

    def __init__(self, state):
        super().__init__()
        self.setObjectName("screen")
        self.state = state
        self._web = None            # lazy QWebEngineView
        self._profile = None        # lazy persistent QWebEngineProfile
        self._cookies: dict[str, str] = {}
        self._last_sent: dict | None = None   # dedup redundant session hand-offs
        self._connected = False               # drives the adaptive header (#27)
        self._login_mode = False              # True only while the browser is a login
        self._auto_login_armed = False        # armed on tab show → auto-open login
        self._auto_sync_armed = False         # armed on tab show → sync if stale
        self._syncing = False                 # a sync_now is in flight
        self._last_dismissed: tuple[str, int] | None = None   # (kind, id) for Undo
        self._undo_timer = None               # QTimer that clears the undo bar
        # Login-memory (audit): remember-by-default; the CAS form is polled while
        # the login view is open so a successful login can be saved to keyring.
        self._remember_login = True
        self._pending_creds = {"u": "", "p": ""}
        self._cred_timer: QTimer | None = None
        # #44 autofill: the once-per-session auto-submit budget, plus a per-login
        # memo of the keyring read so we don't hit the Secret Service on every
        # single page load of a multi-step CAS + Duo flow.
        self._cal_state: dict = {}
        self._review_items: dict = {"removals": None, "proposals": None}
        self._sync_switch = None
        self._ai_switch = None
        self._policy = cl.AutoSubmitPolicy()
        self._creds_memo: tuple | None = None
        self._creds_loaded = False

        outer = vbox(self, (0, 0, 0, 0), 0)

        # --- fixed header: title + status + connection controls -------------
        head = QWidget()
        hv = vbox(head, (34, 26, 34, 18), 0)
        self._status_pill = _StatusPill()
        hv.addWidget(section_head("Canvas", self._status_pill))

        # Only the connected-state actions live in the header now; connecting is
        # owned by the hero card so there's a single, unambiguous CTA (audit #2).
        controls = hbox(m=(0, 14, 0, 0), s=10)
        self._browse_btn = button("Browse Canvas ↗", "primary", px=12, height=30)
        self._browse_btn.clicked.connect(self._start_browse)
        self._sync_btn = button("Sync now", "soft", px=12, height=30)
        self._sync_btn.clicked.connect(self._sync_now)
        self._manage_btn = button("Settings", "soft", px=12, height=30)
        self._manage_btn.clicked.connect(self._open_manage)
        self._disconnect_btn = button("Disconnect", "ghost", px=12, height=30)
        self._disconnect_btn.clicked.connect(self._disconnect)
        for w in (self._browse_btn, self._sync_btn, self._manage_btn,
                  self._disconnect_btn):
            controls.addWidget(w)
        controls.addStretch(1)
        self._controls_row = controls
        hv.addLayout(controls)
        outer.addWidget(head)
        self._sync_controls()                    # start in the disconnected layout

        # --- swap between synced content and the login browser --------------
        self._stack = QStackedWidget()
        outer.addWidget(self._stack, 1)

        # content view (index 0)
        self._inner = QWidget()
        cv = vbox(self._inner, (34, 6, 34, 40), 0)
        # Undo bar for a just-dismissed item — lives above the lists so a content
        # refresh (which clears the lists) leaves it standing.
        self._undo_box = vbox(s=0)
        cv.addLayout(self._undo_box)
        # Pending-calendar strip (audit #4): its own full-width callout above the
        # list, populated independently by the pending-markers reply.
        self._cal_box = vbox(s=0)
        cv.addLayout(self._cal_box)
        # Separate boxes so refreshing one strip never clears the others.
        self._queue_box = vbox(s=0)
        cv.addLayout(self._queue_box)
        self._prop_box = vbox(s=0)
        cv.addLayout(self._prop_box)
        self._alert_box = vbox(s=0)
        cv.addLayout(self._alert_box)
        self._assign_box = vbox(s=0)
        cv.addLayout(self._assign_box)
        cv.addSpacing(22)
        self._ann_box = vbox(s=0)
        cv.addLayout(self._ann_box)
        cv.addStretch(1)
        self._stack.addWidget(scroll(self._inner))

        # browser view (index 1) — a slim nav toolbar above the lazy web view.
        # Doubles as the login view: Connect navigates it to /login, Browse to
        # the dashboard, an assignment's row-click to its page (#28).
        self._host = QWidget()
        host_v = vbox(self._host, (0, 0, 0, 0), 0)
        tb = hbox(m=(12, 8, 14, 8), s=6)
        self._back_btn = IconButton("chevron-left", 26, self._nav_back, "Back")
        self._fwd_btn = IconButton("chevron-right", 26, self._nav_fwd, "Forward")
        self._reload_btn = IconButton("refresh", 26, self._nav_reload, "Reload")
        self._url_lbl = ElideLabel("", 11, T.TEXT_MUTED, mono=True)
        done = button("Done", "ghost", px=12, height=28)
        done.clicked.connect(self._done_browsing)
        for w in (self._back_btn, self._fwd_btn, self._reload_btn):
            tb.addWidget(w)
        tb.addWidget(self._url_lbl, 1)
        # Autofill diagnostics (#44), off unless LUMEN_CANVAS_DEBUG=1. The live
        # CAS form is the one thing no test here can reach, so Probe turns a
        # failed login into a pasteable description of the real form instead of
        # another round of guessing at selectors.
        self._debug_btns = []
        if DEBUG_AUTOFILL:
            for text, slot, tip in (
                    ("Probe", self._probe_form, "Dump this page's form fields"),
                    ("Test fill", self._test_fill, "Fill without submitting")):
                b = button(text, "ghost", px=11, height=28)
                b.setToolTip(tip)
                b.clicked.connect(slot)
                self._debug_btns.append(b)
                tb.addWidget(b)
        tb.addWidget(done)
        host_v.addLayout(tb)
        host_v.addWidget(hline(T.BORDER_FIELD))
        self._web_area = QWidget()
        self._host_layout = QVBoxLayout(self._web_area)
        self._host_layout.setContentsMargins(0, 0, 0, 0)
        host_v.addWidget(self._web_area, 1)
        self._stack.addWidget(self._host)

        # Canvas settings (index 2) — the calendar switches, then the archive
        # on/off panel (#28). Deliberately NOT in screens/settings.py: there is
        # no settings.set route, its _row primitive is config-file-shaped, and
        # these prefs are Canvas-owned — the same reasoning as set_course_included.
        manage = QWidget()
        mv = vbox(manage, (34, 26, 34, 40), 0)
        mhead = hbox(m=(0, 0, 0, 6), s=10)
        mhead.addWidget(label("Canvas settings", 17, T.TEXT_PRIMARY, 600))
        mhead.addStretch(1)
        mdone = button("Done", "ghost", px=12, height=28)   # matches the browser's Done (audit #5)
        mdone.clicked.connect(self._close_manage)
        mhead.addWidget(mdone)
        mv.addLayout(mhead)

        mv.addSpacing(8)
        mv.addWidget(eyebrow("Calendar"))
        mv.addSpacing(6)
        cal_panel = QFrame()
        cal_panel.setProperty("role", "panel")
        cp = vbox(cal_panel, (14, 4, 14, 6), 0)
        self._sync_switch = self._switch_row(
            cp, "Put due dates on my calendar",
            "Adds a 15-minute block ending at each due time, and keeps it in "
            "step as Canvas changes.", self._toggle_calendar_sync)
        cp.addWidget(hline(T.BORDER_FIELD))
        self._ai_switch = self._switch_row(
            cp, "Lumen-powered details",
            "Adds a short summary and colour by type, and offers exam dates it "
            "finds in announcements for you to approve.",
            self._toggle_calendar_ai)
        mv.addWidget(cal_panel)

        mv.addSpacing(20)
        mv.addWidget(eyebrow("Courses"))
        mv.addSpacing(6)
        mv.addWidget(label(
            "Turn off classes you're done with — Lumen hides them and stops "
            "pulling new data. Nothing you've already saved is lost.",
            12.5, T.TEXT_MUTED, wrap=True))
        mv.addSpacing(14)
        self._course_box = vbox(s=0)
        mv.addLayout(self._course_box)
        mv.addStretch(1)
        self._manage = manage
        self._stack.addWidget(scroll(self._manage))

        # Review view (index 3) — removals and AI proposals, item by item.
        review = QWidget()
        rv = vbox(review, (34, 26, 34, 40), 0)
        rhead = hbox(m=(0, 0, 0, 6), s=10)
        rhead.addWidget(label("Review", 17, T.TEXT_PRIMARY, 600))
        rhead.addStretch(1)
        self._review_all_btn = button("Remove all", "soft", px=12, height=28)
        self._review_all_btn.clicked.connect(self._remove_all)
        rhead.addWidget(self._review_all_btn)
        rdone = button("Done", "ghost", px=12, height=28)
        rdone.clicked.connect(self._close_review)
        rhead.addWidget(rdone)
        rv.addLayout(rhead)
        self._review_hint = label("", 12.5, T.TEXT_MUTED, wrap=True)
        rv.addWidget(self._review_hint)
        rv.addSpacing(14)
        self._review_box = vbox(s=0)
        rv.addLayout(self._review_box)
        rv.addStretch(1)
        self._review = review
        self._stack.addWidget(scroll(self._review))

        self._refresh_status()
        self._refresh_content()

    # ---- ask-bar context -------------------------------------------------
    def context(self) -> dict:
        return {"screen": "canvas"}

    def showEvent(self, ev):
        # Re-read the daemon's truth every time the tab is shown, so a poll that
        # completed while the tab was hidden surfaces its "last sync" + content.
        super().showEvent(ev)
        # Arm auto-connect: if the status callback comes back disconnected, open
        # the login for the user (their choice — every visit while disconnected).
        # Armed only here so a Disconnect click doesn't relaunch the login.
        self._auto_login_armed = True
        # Arm the freshness check too: _apply_status has the daemon's last_sync,
        # so it can decide whether opening the tab should pull (#28 follow-up).
        self._auto_sync_armed = True
        self._refresh_status()
        self._refresh_content()

    # ---- assignments + announcements content ----------------------------
    def _refresh_content(self):
        self._refresh_calendar()          # first: it decides what else shows
        self.state.canvas_assignments(self._render_assignments)
        self.state.canvas_announcements(self._render_announcements)
        self.state.canvas_pending_calendar(self._render_pending)

    def _render_assignments(self, res):
        clear_layout(self._assign_box)
        items = (res or {}).get("assignments", [])

        head = hbox(m=(0, 0, 0, 12), s=10)
        head.addWidget(eyebrow("Assignments"))
        head.addStretch(1)
        head.addWidget(label(f"{len(items)}" if items else "0", 10,
                             T.TEXT_FAINT, mono=True))
        self._assign_box.addLayout(head)

        if not items:
            if not self._connected:
                self._assign_box.addWidget(self._connect_hero())
            else:
                self._assign_box.addWidget(
                    empty_state("No assignments synced yet.",
                                "They'll appear here after the next Canvas sync."))
            return

        # A due-date timeline: soonest-first, split into time sections, and
        # within each section consecutive same-course assignments bond into one
        # course card while a course change starts a fresh card (#28 rework).
        section = -1
        cur_v = None
        last_course = None
        first_in_card = True
        for a in sorted(items, key=_due_key):
            b = _time_bucket(a.get("due_at", ""))
            if b != section:
                section = b
                last_course = None
                if b:                       # no lead gap before the first section
                    self._assign_box.addSpacing(4)
                self._assign_box.addWidget(eyebrow(_SECTIONS[b]))
                self._assign_box.addSpacing(6)
            code = a.get("course_code") or "Canvas"
            if code != last_course:
                last_course = code
                card, cur_v = self._card(T.label_color(code))
                cur_v.addLayout(self._cluster_header(code, a.get("course_name")))
                self._assign_box.addWidget(card)
                self._assign_box.addSpacing(8)
                first_in_card = True
            if not first_in_card:
                cur_v.addWidget(hline(T.BORDER_FIELD))
            cur_v.addWidget(self._assignment_row(a))
            first_in_card = False

    def _card(self, accent_color: str, on_click=None):
        """A role=panel card with a course-coloured left rail. Returns (frame,
        content_vbox); the caller fills the vbox. A wider (5px) rail so course
        identity actually reads (audit #6). `on_click` makes the whole card a
        target — used by announcement cards; assignment clusters click per-row."""
        f = ClickRow(on_click) if on_click else QFrame()
        f.setProperty("role", "panel")
        h = hbox(f, (0, 0, 0, 0), 0)
        h.addWidget(AccentBar(on=True, w=5, color=accent_color))
        inner = QWidget()
        v = vbox(inner, (14, 10, 14, 12), 0)
        h.addWidget(inner, 1)
        return f, v

    def _course_chip(self, code: str) -> Chip:
        """The course-code pill — tinted with the course colour so each course is
        recognisable at a glance (audit #6)."""
        c = T.label_color(code)
        return Chip(code, c, _tint(c, 0.45), bg=_tint(c, 0.10), px=9,
                    hpad=6, vpad=2, mono=True, weight=600)

    def _cluster_header(self, code: str, name):
        """The course-code chip + full course name atop an assignment card."""
        row = hbox(m=(0, 0, 0, 8), s=8)
        row.addWidget(self._course_chip(code))
        if name:
            row.addWidget(ElideLabel(name, 12, T.TEXT_MUTED), 1)
        else:
            row.addStretch(1)
        return row

    def _assignment_row(self, a: dict) -> ClickRow:
        """One assignment inside a course card: name on top, a meta line of
        grade/points · due · status below. The whole row opens the assignment in
        the in-app browser; a faint ✕ dismisses it (audit #3 — no more per-row
        button column)."""
        url = a.get("html_url")
        row = ClickRow((lambda u=url: self._open_in_browser(u)) if url else None)
        if url:
            row.setToolTip("Open in Canvas")
        v = vbox(row, (0, 8, 0, 8), 5)

        top = hbox(s=8)
        top.addWidget(ElideLabel(a.get("name", ""), 13.5, T.TEXT_PRIMARY), 1)
        if url:
            top.addWidget(label("↗", 12, T.TEXT_GHOST))   # affordance; row handles the click
        self._add_dismiss(top, "assignment", a["id"], a.get("name", ""))
        v.addLayout(top)

        meta = hbox(s=7)
        score = a.get("score")
        pts = _num(a.get("points"))
        done = a.get("submitted") or score is not None
        if score is not None:                # graded — the number that matters
            grade = f"{_num(score)} / {pts}" if pts else f"{_num(score)} pt"
            meta.addWidget(Chip(grade, T.TEXT_PRIMARY, T.BORDER_MED, px=10,
                                radius=3, hpad=7, vpad=2, weight=600))
        elif pts:
            meta.addWidget(Chip(f"{pts} pt", T.TEXT_MUTED, T.BORDER_FIELD, px=10,
                                radius=3, hpad=7, vpad=2))
        text, fg, border = _due_meta(a.get("due_at", ""), done=bool(done))
        meta.addWidget(Chip(text, fg, border, px=10, radius=3, hpad=7, vpad=2))
        chip = self._status_chip(a)
        if chip is not None:
            meta.addWidget(chip)
        if a.get("calendar_event_id"):
            cal = Chip("on calendar", T.TEXT_MUTED, T.BORDER_FIELD, px=10,
                       radius=3, hpad=7, vpad=2)
            cal.setToolTip("Lumen is keeping this due date on your calendar")
            meta.addWidget(cal)
        meta.addStretch(1)
        v.addLayout(meta)
        return row

    def _status_chip(self, a: dict) -> Chip | None:
        """The submission chip, shown only when it says something:
        graded items already carry their score, so no chip; submitted → a quiet
        '✓ submitted'; an overdue item that's still not turned in → red. A
        not-yet-due item that isn't submitted shows nothing — that's expected,
        not news (audit #10)."""
        if a.get("score") is not None:
            return None
        if a.get("submitted"):
            return Chip("✓ submitted", T.OK, T.BORDER_OK, px=10, radius=3,
                        hpad=7, vpad=2)
        iso = (a.get("due_at") or "")[:10]
        try:
            overdue = date.fromisoformat(iso) < date.today()
        except ValueError:
            overdue = False
        if not overdue:
            return None
        return Chip("not turned in", _OVERDUE, T.BORDER_DANGER, px=10, radius=3,
                    hpad=7, vpad=2)

    def _add_dismiss(self, row, kind: str, item_id: int, name: str):
        """A trailing faint '✕' that hides one assignment/announcement from the
        tab, with an Undo bar (gone-but-restorable). A ClickLabel so it swallows
        its own click and never triggers the row's open."""
        x = ClickLabel("✕", 12, T.TEXT_GHOST,
                       on_click=lambda k=kind, i=item_id, n=name: self._dismiss(k, i, n),
                       tooltip="Dismiss")
        row.addWidget(x)

    def _connect_hero(self) -> QWidget:
        """A focused welcome card for the disconnected state (#27) — the single
        place to connect, plus the remember toggle and (if saved) a forget link,
        so nothing about connecting is duplicated in the header (audit #2)."""
        f = QFrame()
        f.setProperty("role", "panel")
        v = vbox(f, (26, 26, 26, 26), 10)
        v.addWidget(label("Bring your coursework into Lumen", 17,
                          T.TEXT_PRIMARY, 600))
        v.addWidget(label(
            "Connect Canvas to sync your assignments and announcements. Due "
            "dates can flow into your calendar and announcements into your "
            "todos — all on-device.", 13, T.TEXT_MUTED, wrap=True))
        v.addSpacing(4)
        cta_row = hbox(s=12)
        cta = button("Connect Canvas", "primary", px=13, height=32)
        cta.clicked.connect(self._start_login)
        cta_row.addWidget(cta)
        remember = QCheckBox("Remember my login")
        remember.setChecked(self._remember_login)
        remember.toggled.connect(self._set_remember)
        cta_row.addWidget(remember)
        cta_row.addStretch(1)
        v.addLayout(cta_row)
        if self._has_saved_login():
            forget = ClickLabel("Forget saved login", 12, T.TEXT_FAINT,
                                 on_click=self._forget)
            v.addWidget(forget)
        return f

    def _set_remember(self, on: bool):
        self._remember_login = bool(on)

    @staticmethod
    def _has_saved_login() -> bool:
        try:
            return canvas_creds.load() is not None
        except Exception:
            return False

    def _render_pending(self, res):
        """The pending-calendar callout: a full-width strip above the list, only
        when a poll left due dates that aren't on the calendar yet (audit #4)."""
        clear_layout(self._cal_box)
        n = len((res or {}).get("markers", []))
        if not n or not self._connected:
            return
        # Automatic sync owns the calendar once it's on, so the old manual push
        # is both redundant and confusing next to it — two buttons that look
        # like they do the same thing, one of which quietly does less (it only
        # covers assignments that happen to have a linked todo).
        if self._cal_state.get("sync"):
            return
        f = QFrame()
        f.setProperty("role", "panel")
        row = hbox(f, (14, 10, 14, 10), 10)
        row.addWidget(Dot(6, T.ACCENT))
        plural = "" if n == 1 else "s"
        row.addWidget(label(f"{n} due date{plural} not on your calendar yet",
                            12.5, T.TEXT_SECONDARY), 1)
        add = button("Add to calendar", "soft", px=11, height=26)
        add.clicked.connect(self._push_due_dates)
        row.addWidget(add)
        self._cal_box.addWidget(f)
        self._cal_box.addSpacing(16)

    def _render_announcements(self, res):
        clear_layout(self._ann_box)
        items = (res or {}).get("announcements", [])

        # When disconnected the assignments hero already owns the whole screen —
        # don't leave a lone "Announcements · 0" header dangling under it
        # (audit #1). Only render this section once there's a connection.
        if not self._connected:
            return

        head = hbox(m=(0, 0, 0, 12), s=10)
        head.addWidget(eyebrow("Announcements"))
        head.addStretch(1)
        head.addWidget(label(f"{len(items)}" if items else "0", 10,
                             T.TEXT_FAINT, mono=True))
        self._ann_box.addLayout(head)

        if not items:
            self._ann_box.addWidget(empty_state("No announcements yet."))
            return

        # A feed of cards, newest first: course + when on top, then the title and
        # a couple of plain-text lines of the body so it reads as news, not a
        # bare headline list (#28 rework). The whole card opens the post.
        for i, a in enumerate(items):
            if i:
                self._ann_box.addSpacing(8)
            self._ann_box.addWidget(self._announcement_card(a))

    def _announcement_card(self, a: dict) -> QFrame:
        code = a.get("course_code") or "Canvas"
        url = a.get("html_url")
        card, v = self._card(T.label_color(code),
                             on_click=(lambda u=url: self._open_in_browser(u)) if url else None)
        if url:
            card.setToolTip("Open in Canvas")

        head = hbox(s=8)
        head.addWidget(self._course_chip(code))
        ago = _ago(a.get("posted_at", ""))
        if ago:
            head.addWidget(label(ago, 10, T.TEXT_FAINT, mono=True))
        head.addStretch(1)
        if a.get("actionable") and not a.get("todo_id"):
            btn = button("Add as todo", "soft", px=11, height=24)
            btn.clicked.connect(
                lambda _, i=a["id"]: self._add_announcement_todo(i))
            head.addWidget(btn)
        elif a.get("todo_id"):
            head.addWidget(label("✓ added", 11, T.OK, mono=True))
        self._add_dismiss(head, "announcement", a["id"], a.get("title", ""))
        v.addLayout(head)

        v.addSpacing(7)
        v.addWidget(label(a.get("title", ""), 13.5, T.TEXT_PRIMARY, 600,
                          wrap=True))
        preview = _strip_html(a.get("message", ""))
        if preview:
            v.addSpacing(3)
            v.addWidget(label(preview, 12, T.TEXT_MUTED, wrap=True))
        return card

    def _add_announcement_todo(self, ann_id: int):
        self.state.canvas_add_announcement_todo(
            ann_id, lambda _r: self._refresh_content())

    def _push_due_dates(self):
        self.state.canvas_push_due_dates(lambda _r: self._refresh_content())

    # ---- dismiss / undo --------------------------------------------------
    def _dismiss(self, kind: str, item_id: int, name: str):
        """Hide one item (assignment/announcement) from the tab and offer Undo.
        The daemon flag survives sync; nothing is deleted."""
        self._last_dismissed = (kind, item_id)
        self._set_dismissed(kind, item_id, True)
        self._show_undo_bar(name)

    def _undo_dismiss(self):
        if self._last_dismissed is None:
            self._clear_undo_bar()
            return
        kind, item_id = self._last_dismissed
        self._set_dismissed(kind, item_id, False)
        self._clear_undo_bar()

    def _set_dismissed(self, kind: str, item_id: int, dismissed: bool):
        cb = lambda _r: self._refresh_content()
        if kind == "assignment":
            self.state.canvas_dismiss_assignment(item_id, dismissed, cb)
        else:
            self.state.canvas_dismiss_announcement(item_id, dismissed, cb)

    def _show_undo_bar(self, name: str):
        clear_layout(self._undo_box)
        f = QFrame()
        f.setProperty("role", "panel")
        v = hbox(f, (14, 8, 10, 8), 10)
        shown = (name or "Item").strip()
        if len(shown) > 48:
            shown = shown[:47] + "…"
        v.addWidget(label(f"“{shown}” dismissed", 12.5, T.TEXT_MUTED), 1)
        undo = button("Undo", "soft", px=11, height=24)
        undo.clicked.connect(self._undo_dismiss)
        v.addWidget(undo)
        self._undo_box.addWidget(f)
        self._undo_box.addSpacing(14)
        if self._undo_timer is not None:
            self._undo_timer.stop()
        self._undo_timer = QTimer(self)
        self._undo_timer.setSingleShot(True)
        self._undo_timer.timeout.connect(self._clear_undo_bar)
        self._undo_timer.start(6000)

    def _clear_undo_bar(self):
        if self._undo_timer is not None:
            self._undo_timer.stop()
            self._undo_timer = None
        self._last_dismissed = None
        clear_layout(self._undo_box)

    # ---- status ----------------------------------------------------------
    def _refresh_status(self):
        self.state.canvas_status(self._apply_status)

    def _sync_now(self):
        """Force a pull now — the button, and the tab-open freshness check. The
        daemon guards against overlapping syncs; this guards the pill."""
        if self._syncing:
            return
        self._syncing = True
        self._sync_btn.setEnabled(False)
        self._status_pill.set_status("syncing…", T.TEXT_MUTED, T.WARN)
        self.state.canvas_sync_now(self._on_synced)

    def _on_synced(self, res):
        self._syncing = False
        self._sync_btn.setEnabled(True)
        if isinstance(res, dict) and res.get("ok") is False and res.get("started"):
            # A sync that ran but pulled nothing (dead session, network error)
            # still has to say so, or the pill sits on "syncing…" forever.
            self._status_pill.set_status("sync failed", T.WARN, T.WARN)
        else:
            self._refresh_status()
        self._refresh_content()

    def _sync_controls(self):
        """Show the header actions only when connected (#27, #28): Browse Canvas
        + Settings + Disconnect. Connecting lives entirely in the hero, so
        there's nothing to show here while disconnected (audit #2)."""
        for w in (self._browse_btn, self._sync_btn, self._manage_btn,
                  self._disconnect_btn):
            w.setVisible(self._connected)

    def _apply_status(self, st):
        # Shared by canvas.status / set_session / disconnect — all reply with
        # this same {connected,last_sync,...} shape as a result. Updates the pill
        # + controls only; it deliberately never changes the visible page, so a
        # background session refresh while browsing can't yank the user out of
        # the in-app browser (#28). Returning to content after a *login* is
        # handled by _on_connected, gated on _login_mode.
        connected = isinstance(st, dict) and st.get("connected")
        self._connected = bool(connected)
        if connected:
            last = st.get("last_sync")
            if self._syncing:
                self._status_pill.set_status("syncing…", T.TEXT_MUTED, T.WARN)
            else:
                self._status_pill.set_status(
                    f"last sync {_ago(last) or '—'}", T.OK, T.OK)
            # Opening the tab pulls when the mirror is stale (or was never
            # pulled). Connecting just after a poll tick otherwise left the tab
            # empty for a whole interval (live bug 2026-08-25).
            if self._auto_sync_armed:
                self._auto_sync_armed = False
                if _stale(last):
                    self._sync_now()
        else:
            self._status_pill.set_status("Not connected", T.TEXT_MUTED,
                                         T.TEXT_GHOST)
            # Auto-open the login when the tab was just shown and we're not
            # connected — but only from the content page, never over an open
            # browser/login view.
            if self._auto_login_armed and self._stack.currentIndex() == self._CONTENT_IDX:
                self._auto_login_armed = False
                self._start_login()
        self._sync_controls()

    # ---- browser + login flow -------------------------------------------
    def _ensure_web(self):
        """Build the lazy web view on first use — kept out of __init__ so simply
        constructing the screen (headless tests, the screenshot script) never
        spins up Chromium (#28)."""
        if self._web is not None:
            return
        from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
        from PyQt6.QtWebEngineWidgets import QWebEngineView

        class _Page(QWebEnginePage):
            """Force target=_blank / window.open links to load in this same view
            instead of a throwaway popup Qt instantly discards (the links that
            "open for a split second then close" (#28))."""
            def createWindow(self, _type):
                return self

        # Named profile → persistent on disk: stay logged in across restarts.
        self._profile = QWebEngineProfile("lumen-canvas", self)
        self._profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        self._profile.cookieStore().cookieAdded.connect(self._on_cookie)
        self._profile.downloadRequested.connect(self._on_download)
        self._web = QWebEngineView(self)
        # The page owns two-finger scrolling inside it: without this the app's
        # SwipeNavigator filtered the gesture's phase events and Chromium never
        # scrolled at all (#28 follow-up).
        self._web.setProperty("lumen_swipe_ignore", True)
        self._web.setPage(_Page(self._profile, self._web))
        self._web.loadFinished.connect(self._on_load_finished)
        self._web.urlChanged.connect(self._on_url_changed)
        self._host_layout.addWidget(self._web)

    def _on_download(self, item):
        """Save a Canvas file download to ~/Downloads instead of silently
        dropping it (clicking a PDF/file used to do nothing) (#28)."""
        from pathlib import Path
        try:
            item.setDownloadDirectory(str(Path.home() / "Downloads"))
            item.accept()
        except Exception:
            log.exception("canvas download failed")

    def _open_in_browser(self, url: str, login: bool = False):
        """Reveal the in-app browser at `url` (building it on first use). The
        single entry point for Connect, Browse, and every row-click (#28).
        `login` marks the login flow so a successful session hand-off returns to
        the content list; browsing stays put."""
        self._login_mode = login
        self._ensure_web()
        self._web.setUrl(QUrl(url))
        self._stack.setCurrentIndex(self._BROWSER_IDX)

    def _start_login(self):
        # Fresh capture buffer + poll the CAS form so a remembered login can be
        # saved once it succeeds.
        self._pending_creds = {"u": "", "p": ""}
        self._reset_autofill()
        self._start_cred_poll()
        self._open_in_browser(_CANVAS_BASE + "/login", login=True)

    def _start_browse(self):
        self._open_in_browser(_CANVAS_BASE)

    def _nav_back(self):
        if self._web is not None:
            self._web.back()

    def _nav_fwd(self):
        if self._web is not None:
            self._web.forward()

    def _nav_reload(self):
        if self._web is not None:
            self._web.reload()

    def _done_browsing(self):
        self._stop_cred_poll()
        self._stack.setCurrentIndex(self._CONTENT_IDX)

    def _on_url_changed(self, qurl):
        self._url_lbl.setText(qurl.toString())
        if self._web is not None:
            h = self._web.history()
            self._back_btn.setEnabled(h.canGoBack())
            self._fwd_btn.setEnabled(h.canGoForward())

    # ---- login-memory capture -------------------------------------------
    def _start_cred_poll(self):
        """While the login view is open, read whatever the user has typed into
        the CAS form every second, caching the latest non-empty uNID/password so
        a successful login (detected in _on_cookie) can be saved. Cheap, and it
        survives the multi-page CAS flow (each page contributes its field)."""
        if self._cred_timer is not None:
            return
        self._cred_timer = QTimer(self)
        self._cred_timer.timeout.connect(self._poll_cred_fields)
        self._cred_timer.start(1000)

    def _stop_cred_poll(self):
        if self._cred_timer is not None:
            self._cred_timer.stop()
            self._cred_timer = None

    def _poll_cred_fields(self):
        if self._web is None or not self._login_mode:
            return
        self._web.page().runJavaScript(cl.read_fields_js(), self._cache_cred_fields)
        # autofill_js retries asynchronously, so its own return value is only
        # attempt 1. Riding the existing 1s timer picks up the settled report —
        # which is what tells the policy a password field came back.
        self._web.page().runJavaScript(cl.autofill_report_js(),
                                       self._on_autofill_report)

    def _on_autofill_report(self, raw):
        res = cl.parse_autofill_result(raw)
        if res["tries"]:
            self._policy.record(res)

    def _cache_cred_fields(self, result):
        try:
            data = json.loads(result) if result else {}
        except (ValueError, TypeError):
            return
        if data.get("u"):
            self._pending_creds["u"] = data["u"]
        if data.get("p"):
            self._pending_creds["p"] = data["p"]

    def _maybe_save_login(self):
        """On a confirmed login, persist the captured credentials if the user
        asked to be remembered. Best-effort — a keyring miss is non-fatal."""
        if not self._remember_login:
            return
        u, p = self._pending_creds.get("u"), self._pending_creds.get("p")
        if not u or not p:
            return
        try:
            canvas_creds.save(u, p)
            log.info("canvas login saved to keyring (remember)")
        except Exception:
            log.exception("canvas keyring save failed")
        self._pending_creds = {"u": "", "p": ""}

    # ---- manage courses (archive on/off) --------------------------------
    def _open_manage(self):
        self.state.canvas_courses(self._render_courses)
        self._refresh_calendar()
        self._stack.setCurrentIndex(self._MANAGE_IDX)

    def _render_courses(self, res):
        clear_layout(self._course_box)
        courses = (res or {}).get("courses", [])
        if not courses:
            self._course_box.addWidget(empty_state(
                "No courses yet.",
                "They'll appear here after the first Canvas sync."))
            return
        panel = QFrame()
        panel.setProperty("role", "panel")
        pv = vbox(panel, (14, 4, 14, 6), 0)
        for i, c in enumerate(courses):
            if i:
                pv.addWidget(hline(T.BORDER_FIELD))
            row = hbox(m=(0, 12, 0, 12), s=12)
            code = c.get("course_code") or "Canvas"
            row.addWidget(self._course_chip(code))
            row.addWidget(ElideLabel(c.get("name", ""), 13.5, T.TEXT_PRIMARY), 1)
            sw = Switch(bool(c.get("included", 1)))
            sw.toggled.connect(
                lambda on, cid=c["id"]: self._toggle_course(cid, on))
            row.addWidget(sw)
            pv.addLayout(row)
        self._course_box.addWidget(panel)

    # ---- calendar sync settings + review ---------------------------------
    def _switch_row(self, parent_v, title_text: str, hint: str, on_toggle):
        """One labelled Switch row inside the settings panel. Returns the Switch
        so the caller can set it from the daemon's truth without re-emitting."""
        row = hbox(m=(0, 12, 0, 12), s=12)
        col = vbox(m=(0, 0, 0, 0), s=2)
        col.addWidget(label(title_text, 13.5, T.TEXT_PRIMARY))
        col.addWidget(label(hint, 11.5, T.TEXT_MUTED, wrap=True))
        row.addLayout(col, 1)
        sw = Switch(False)
        sw.toggled.connect(on_toggle)
        row.addWidget(sw)
        parent_v.addLayout(row)
        return sw

    def _apply_calendar_status(self, res):
        st = res or {}
        self._cal_state = dict(st)
        # blockSignals: setting the switch from the daemon's answer must not
        # look like a user toggle and bounce a write straight back.
        for sw, key in ((self._sync_switch, "sync"), (self._ai_switch, "ai")):
            if sw is None:
                continue
            sw.blockSignals(True)
            sw.setChecked(bool(st.get(key)))
            sw.blockSignals(False)
        self._ai_switch.setEnabled(bool(st.get("sync")))
        self._render_queue_strip(int(st.get("queued") or 0))
        self._render_proposal_strip(int(st.get("proposals") or 0))
        self._render_alert_strip(int(st.get("alerts") or 0))
        # These callbacks race with _render_pending's, so whichever lands second
        # has to be right on its own: if sync is on, the manual push strip goes,
        # whatever order the answers arrived in.
        if st.get("sync"):
            clear_layout(self._cal_box)

    def _render_alert_strip(self, n: int):
        """Auto-updates are silent by design; this is how they stay *visible*
        after the fact — a moved due date or a clash you'd otherwise never know
        Lumen had noticed."""
        clear_layout(self._alert_box)
        if not n or not self._connected:
            return
        f = QFrame()
        f.setProperty("role", "panel")
        row = hbox(f, (14, 8, 14, 8), 10)
        row.addWidget(Dot(6, T.TEXT_MUTED))
        plural = "" if n == 1 else "s"
        row.addWidget(label(f"{n} calendar change{plural} since you last looked",
                            12.5, T.TEXT_MUTED), 1)
        self._alert_box.addWidget(f)
        self._alert_box.addSpacing(14)

    def _toggle_calendar_sync(self, on: bool):
        self.state.canvas_set_calendar_sync(bool(on), self._after_calendar_toggle)

    def _toggle_calendar_ai(self, on: bool):
        self.state.canvas_set_calendar_ai(bool(on), self._after_calendar_toggle)

    def _after_calendar_toggle(self, res):
        """A toggle has to do something visible without waiting out the poll,
        so ask the daemon to sync now and re-read the status after."""
        self._apply_calendar_status({**self._cal_state, **(res or {})})
        self.state.canvas_sync_now(lambda _r: self._refresh_calendar())

    def _refresh_calendar(self):
        self.state.canvas_calendar_status(self._apply_calendar_status)

    def _render_queue_strip(self, n: int):
        clear_layout(self._queue_box)
        if not n or not self._connected:
            return
        plural = "" if n == 1 else "s"
        self._queue_box.addWidget(self._strip(
            f"{n} calendar event{plural} to remove", "Review",
            self._open_review, T.WARN))
        self._queue_box.addSpacing(16)

    def _render_proposal_strip(self, n: int):
        clear_layout(self._prop_box)
        if not n or not self._connected:
            return
        plural = "" if n == 1 else "s"
        self._prop_box.addWidget(self._strip(
            f"{n} suggested event{plural} from Lumen", "Review",
            self._open_review, T.ACCENT))
        self._prop_box.addSpacing(16)

    def _strip(self, text: str, action: str, on_click, dot_color: str):
        f = QFrame()
        f.setProperty("role", "panel")
        row = hbox(f, (14, 10, 14, 10), 10)
        row.addWidget(Dot(6, dot_color))
        row.addWidget(label(text, 12.5, T.TEXT_SECONDARY), 1)
        btn = button(action, "soft", px=11, height=26)
        btn.clicked.connect(on_click)
        row.addWidget(btn)
        return f

    def _open_review(self):
        self._stack.setCurrentIndex(self._REVIEW_IDX)
        self._refresh_review()

    def _close_review(self):
        self._stack.setCurrentIndex(self._CONTENT_IDX)
        self._refresh_calendar()
        self._refresh_content()

    def _refresh_review(self):
        self._review_items = {"removals": None, "proposals": None}
        self.state.canvas_calendar_queue(
            lambda r: self._collect_review("removals", (r or {}).get("items", [])))
        self.state.canvas_proposals(
            lambda r: self._collect_review("proposals", (r or {}).get("items", [])))

    def _collect_review(self, key: str, items: list):
        """Both lists arrive as separate callbacks; render once both are in so
        the page doesn't flicker through a half-built state."""
        self._review_items[key] = items
        if any(v is None for v in self._review_items.values()):
            return
        self._render_review()

    def _render_review(self):
        clear_layout(self._review_box)
        removals = self._review_items.get("removals") or []
        proposals = self._review_items.get("proposals") or []
        self._review_all_btn.setVisible(bool(removals))
        if not removals and not proposals:
            self._review_box.addWidget(empty_state(
                "Nothing to review.",
                "Removals and Lumen's suggestions show up here."))
            self._review_hint.setText("")
            return
        self._review_hint.setText(
            "Lumen adds and updates due dates on its own. It only asks before "
            "taking something off your calendar, or before adding something it "
            "worked out for itself.")
        if removals:
            self._review_box.addWidget(eyebrow("Remove from calendar"))
            self._review_box.addSpacing(6)
            for item in removals:
                self._review_box.addWidget(self._removal_card(item))
                self._review_box.addSpacing(8)
        if proposals:
            self._review_box.addSpacing(10)
            self._review_box.addWidget(eyebrow("Suggested by Lumen"))
            self._review_box.addSpacing(6)
            for item in proposals:
                self._review_box.addWidget(self._proposal_card(item))
                self._review_box.addSpacing(8)

    def _review_card(self, title_text: str, sub: str, accent: str,
                     yes: str, no: str, on_yes, on_no):
        card, v = self._card(accent)
        v.addWidget(label(title_text, 13.5, T.TEXT_PRIMARY, wrap=True))
        if sub:
            v.addWidget(label(sub, 11.5, T.TEXT_MUTED, wrap=True))
        row = hbox(m=(0, 10, 0, 0), s=8)
        row.addStretch(1)
        keep = button(no, "ghost", px=11, height=26)
        keep.clicked.connect(on_no)
        row.addWidget(keep)
        act = button(yes, "soft", px=11, height=26)
        act.clicked.connect(on_yes)
        row.addWidget(act)
        v.addLayout(row)
        return card

    _REMOVAL_REASONS = {
        "submitted": "You've submitted it",
        "gone": "It's no longer in Canvas",
        "dismissed": "You hid it from this tab",
        "archived": "Its course is switched off",
        "no_due": "It no longer has a due date",
    }

    def _removal_card(self, item: dict):
        qid = item.get("id")
        reason = self._REMOVAL_REASONS.get(item.get("reason"),
                                           item.get("reason") or "")
        return self._review_card(
            item.get("title") or "Calendar event", reason, T.WARN,
            "Remove", "Keep",
            lambda: self._resolve_removal(qid, True),
            lambda: self._resolve_removal(qid, False))

    def _proposal_card(self, item: dict):
        pid = item.get("id")
        when = _pretty_when(item.get("start_at"))
        detail = item.get("detail") or ""
        return self._review_card(
            item.get("title") or "Suggested event",
            " · ".join(x for x in (when, detail) if x), T.ACCENT,
            "Add", "No thanks",
            lambda: self._resolve_proposal(pid, True),
            lambda: self._resolve_proposal(pid, False))

    def _resolve_removal(self, qid, approve: bool):
        self.state.canvas_resolve_removal(qid, approve,
                                          lambda _r: self._after_review())

    def _resolve_proposal(self, pid, approve: bool):
        self.state.canvas_resolve_proposal(pid, approve,
                                           lambda _r: self._after_review())

    def _after_review(self):
        self._refresh_calendar()
        self._refresh_review()

    def _remove_all(self):
        """The one bulk action, and the only place this flow opens a modal —
        per-item clicks are their own confirmation."""
        items = self._review_items.get("removals") or []
        if not items:
            return
        rows = [(self._REMOVAL_REASONS.get(i.get("reason"), ""), i.get("title", ""))
                for i in items[:8]]
        if len(items) > 8:
            rows.append(("+", f"{len(items) - 8} more"))
        payload = {"icon": "▲", "title": "Remove Canvas events",
                   "intro": f"Lumen will delete {len(items)} event"
                            f"{'' if len(items) == 1 else 's'} from your Google "
                            "Calendar.",
                   "rows": rows,
                   "confirm_label": f"Remove {len(items)}"}
        self._confirm(payload, lambda ok: self._do_remove_all(items) if ok else None)

    def _confirm(self, payload: dict, done):
        """Route through the window's ConfirmOverlay when there is one.

        LumenWindow names it `confirm`; `confirm_overlay` is only kept as a
        fallback for hosts that use the older attribute name."""
        win = self.window()
        overlay = getattr(win, "confirm", None) or getattr(
            win, "confirm_overlay", None)
        if overlay is None:
            done(False)
            return
        overlay.open(payload, lambda ok, _p: done(ok))

    def _do_remove_all(self, items: list):
        for item in items:
            self.state.canvas_resolve_removal(item.get("id"), True,
                                              lambda _r: None)
        self._after_review()

    def _toggle_course(self, course_id: int, included: bool):
        self.state.canvas_set_course_included(course_id, bool(included))

    def _close_manage(self):
        self._stack.setCurrentIndex(self._CONTENT_IDX)
        self._refresh_status()
        self._refresh_content()

    def _on_cookie(self, cookie):
        name = bytes(cookie.name()).decode(errors="ignore")
        value = bytes(cookie.value()).decode(errors="ignore")
        # Only Canvas's own cookies. The jar is keyed by NAME alone, so an IdP or
        # Duo cookie sharing a name would overwrite the real session and could
        # trip is_authenticated on its own. That was survivable while the jar
        # only lived in RAM; the daemon now writes it to disk, so forwarding
        # third-party credentials is no longer a merely-theoretical cost.
        try:
            domain = cookie.domain()
        except Exception:
            domain = ""
        if not cl.is_canvas_cookie(domain, _CANVAS_HOST):
            return
        self._cookies[name] = value
        # Hand off only when authenticated AND the jar actually changed:
        # loadAllCookies() replays every stored cookie, so without this guard the
        # session would be re-sent (and logged) a dozen times per page load. A
        # rotated cookie changes the dict, so a genuine new session still forwards.
        if cl.is_authenticated(self._cookies) and self._cookies != self._last_sent:
            self._last_sent = dict(self._cookies)
            log.info("canvas session cookie captured — handing off to daemon")
            self._policy.done()      # authenticated: stop injecting anything
            # A confirmed login: save the remembered credentials, then hand the
            # session to the daemon (its reply flips the stack back to content).
            self._maybe_save_login()
            self.state.canvas_set_session(dict(self._cookies), self._on_connected)

    def _on_connected(self, st):
        self._apply_status(st)
        # Finishing a *login* returns to the content list; a session refresh that
        # fired while merely browsing leaves the browser where it is (#28).
        if self._login_mode and isinstance(st, dict) and st.get("connected"):
            self._login_mode = False
            self._stop_cred_poll()
            self._stack.setCurrentIndex(self._CONTENT_IDX)
        self._refresh_content()

    def _on_load_finished(self, ok: bool):
        if not ok or self._web is None:
            return
        # A returning user's canvas_session is already persisted, so this load
        # fires no fresh Set-Cookie and _on_cookie wouldn't run — force the store
        # to replay persisted cookies through _on_cookie so we still detect the
        # session (loadAllCookies triggers cookieAdded per stored cookie).
        if self._profile is not None:
            self._profile.cookieStore().loadAllCookies()
        # Autofill is a LOGIN affordance. Without this gate it typed the saved
        # password into any Canvas page that happened to carry a password field
        # — a settings page, say — which is both surprising and a way to spend
        # the auto-submit budget on a form that was never a login.
        if not self._login_mode:
            return
        self._inject_autofill()

    # ---- autofill (#44) --------------------------------------------------
    def _saved_creds(self):
        """The keyring read, memoised for the life of one login attempt."""
        if not self._creds_loaded:
            self._creds_loaded = True
            self._creds_memo = canvas_creds.load()   # self-guarded; None on miss
        return self._creds_memo

    def _inject_autofill(self):
        """Fill the CAS form, submitting only while the policy still allows it.
        Everything that can go wrong here is silent by nature — a Qt callback
        swallows exceptions and a missed selector looks identical to a missing
        keyring entry — so every injection reports, and every report is logged."""
        if self._web is None or not self._login_mode:
            return
        creds = self._saved_creds()
        if creds is None:
            log.info("canvas autofill: no saved login — user types it")
            return
        unid, password = creds
        self._web.page().runJavaScript(
            cl.autofill_js(unid, password,
                           allow_password_submit=self._policy.allow_password_submit),
            self._on_autofill_result)

    def _on_autofill_result(self, raw):
        res = cl.parse_autofill_result(raw)
        state = self._policy.record(res)
        log.info("canvas autofill: user=%s pass=%s submitted=%s frames=%d "
                 "blocked=%d tries=%d policy=%s",
                 res["user"] or "-", res["pass"] or "-",
                 res["submitted_password"] or res["submitted_user"],
                 res["frames"], res["blocked_frames"], res["tries"], state)
        if not res["user"] and not res["pass"]:
            log.info("canvas autofill: no login fields matched yet — retrying "
                     "in-page (LUMEN_CANVAS_DEBUG=1 adds a Probe button)")

    # ---- autofill diagnostics (LUMEN_CANVAS_DEBUG=1) ---------------------
    def _probe_form(self):
        if self._web is not None:
            self._web.page().runJavaScript(cl.probe_js(), self._show_probe)

    def _show_probe(self, raw):
        text = raw if isinstance(raw, str) else json.dumps(raw)
        log.info("canvas probe: %s", text)
        try:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(text)
        except Exception:
            log.exception("could not copy the probe to the clipboard")
        self._status_pill.set_status("Probe copied to clipboard", T.OK, T.OK)

    def _test_fill(self):
        """Fill without submitting — safe to repeat against a live form."""
        creds = self._saved_creds()
        if self._web is None or creds is None:
            self._status_pill.set_status("No saved login to test", T.WARN, T.WARN)
            return
        self._web.page().runJavaScript(
            cl.autofill_js(*creds, allow_password_submit=False),
            self._show_test_fill)

    def _show_test_fill(self, raw):
        res = cl.parse_autofill_result(raw)
        log.info("canvas test fill: %s", res)
        hit = res["user"] or res["pass"]
        self._status_pill.set_status(
            f"Filled {res['user'] or '-'} / {res['pass'] or '-'} "
            f"({res['frames']} frame(s))" if hit else "No fields matched",
            T.OK if hit else T.WARN, T.OK if hit else T.WARN)

    # ---- disconnect / forget --------------------------------------------
    def _reset_autofill(self):
        """A new login attempt re-arms the auto-submit budget and re-reads the
        keyring (the user may have just corrected a saved password)."""
        self._policy.reset()
        self._creds_memo = None
        self._creds_loaded = False

    def _disconnect(self):
        self._cookies.clear()
        self._last_sent = None
        self._reset_autofill()
        self._stop_cred_poll()
        if self._profile is not None:
            self._profile.cookieStore().deleteAllCookies()
        self._stack.setCurrentIndex(0)
        self.state.canvas_disconnect(self._apply_status)

    def _forget(self):
        canvas_creds.forget()
        self._status_pill.set_status("Saved login forgotten", T.TEXT_MUTED,
                                     T.TEXT_GHOST)
        self._refresh_content()          # rebuild the hero without the Forget link
