"""Settings: how Lumen answers, how it looks, what it's connected to, the mail
rules it runs, and (under Advanced) the raw config.

Preserves every ui_v3 Settings flow:
  - `state.fetch_settings` on every visit (also re-syncs the model switch),
    `fetch_learned`, `refresh_procedures`, `list_rules`.
  - Model mode off / local / claude via `set_model_mode` (the daemon reply
    moves the UI), Claude sub-model via `set_claude_model`, Claude CLI status
    and 5-hour / 7-day usage. ui_v3 has no confirm step here, so neither does
    this. `model_switch_highlight_requested` flashes the mode control.
  - Accounts: Google reconnect (`google_reconnect`), pause / resume sync
    (`set_connection_enabled`), disconnect (`disconnect_connection`, now
    behind the confirm overlay), Canvas status with a link to the Canvas
    screen.
  - Mail rules: list, enable switch (`toggle_rule`), delete (`delete_rule`),
    edit / new through the rule dialog (`state.open_rule_editor`).
  - Memory: proposed habits (approve / dismiss), active ones (forget), the
    distilled memory text, and "Open memory file" (opens in Files).
  - Background sync: daemon status, stop it, quit Lumen.
  - Read-only config values, MCP servers, config path.
New in v4: theme System / Light / Dark through the theme manager, text size
(stored in QSettings("lumen", "ui_v4") "text_size"), reduce motion.

Text size: ui_v4's foundation has no text-scale hook, so `apply_text_size`
re-applies the app stylesheet with every `font-size: Npx` scaled.
`install_text_size()` applies the saved size and keeps it applied across
theme switches; the library screens call it when they're built.
"""
import re

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QApplication, QBoxLayout, QPlainTextEdit, QWidget

from .. import theme as T
from ..components import (
    Badge, Button, Card, Divider, EmptyState, Eyebrow, Heading, IconButton,
    Label, ScreenHeader, ScrollArea, SegmentedControl, SkeletonRow, Switch,
    SwitchRow, clear_layout, hbox, vbox,
)

MAX_W = 840
NARROW = 640          # content width below which label/control rows stack

# ---- text size (shared with the other library screens) -----------------------
TEXT_SIZES = (50, 75, 100, 125, 150)
_FONT_PX = re.compile(r"font-size:\s*(\d+)px")
_ts_installed = False


def text_size() -> int:
    try:
        pct = int(T.settings().value("text_size", 100, int))
    except (TypeError, ValueError):
        pct = 100
    return pct if pct in TEXT_SIZES else 100


def _scaled_qss(pct: int) -> str:
    from ..styles import build_qss
    k = pct / 100.0
    return _FONT_PX.sub(lambda m: f"font-size: {max(8, round(int(m.group(1)) * k))}px",
                        build_qss(T.current()))


def apply_text_size(pct: int | None = None) -> None:
    """Scale every stylesheet font size. 100% puts the stock sheet back."""
    app = QApplication.instance()
    if app is None:
        return
    pct = text_size() if pct is None else pct
    if pct == 100:
        T.manager().apply(app)
    else:
        app.setStyleSheet(_scaled_qss(pct))


def _reapply_after_theme(_theme=None) -> None:
    if text_size() != 100:
        apply_text_size()


def install_text_size() -> None:
    """Idempotent: apply the saved size once, and again after each theme
    switch (the theme manager re-applies the stock sheet first)."""
    global _ts_installed
    if _ts_installed or QApplication.instance() is None:
        return
    _ts_installed = True
    T.manager().changed.connect(_reapply_after_theme)
    if text_size() != 100:
        apply_text_size()


# ---- small widgets -----------------------------------------------------------
class _Meter(QWidget):
    """A thin usage bar (0..1) painted in theme tokens."""

    def __init__(self, frac: float, width: int = 96):
        super().__init__()
        self._f = max(0.0, min(1.0, float(frac or 0)))
        self.setFixedSize(width, 8)
        T.manager().changed.connect(self.update)

    def paintEvent(self, ev):
        t = T.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(t.color("surface_warm"))
        r = QRectF(self.rect())
        p.drawRoundedRect(r, 4, 4)
        if self._f > 0:
            kind = "danger" if self._f >= 0.9 else ("warn" if self._f >= 0.7
                                                    else "accent")
            p.setBrush(t.color(kind))
            p.drawRoundedRect(QRectF(0, 0, max(8.0, r.width() * self._f),
                                     r.height()), 4, 4)


class _Row(QWidget):
    """Label (+ helper) on the left, control(s) on the right. Stacks when
    the screen is narrow."""

    def __init__(self, title: str, helper: str = "", title_role: str = "body"):
        super().__init__()
        self.setMinimumHeight(T.ROW_H)
        self.box = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.box.setContentsMargins(0, T.S2, 0, T.S2)
        self.box.setSpacing(T.S4)
        col = vbox(s=2)
        self.title = Label(title, title_role, wrap=True)
        col.addWidget(self.title)
        self.helper = Label(helper, "muted", wrap=True)
        self.helper.setVisible(bool(helper))
        col.addWidget(self.helper)
        self.box.addLayout(col, 1)
        self.right = hbox(s=T.S2)
        self.box.addLayout(self.right)

    def add(self, w: QWidget) -> QWidget:
        self.right.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)
        return w

    def set_narrow(self, on: bool) -> None:
        self.box.setDirection(QBoxLayout.Direction.TopToBottom if on
                              else QBoxLayout.Direction.LeftToRight)


_REASON_WORDS = (("from_addrs", "From"), ("domains", "From anyone at"),
                 ("subject_kw", "Subject mentions"), ("body_kw", "Message mentions"))


def _rule_summary(r: dict) -> str:
    bits = []
    for key, words in _REASON_WORDS:
        vals = r.get(key) or []
        if vals:
            if key == "domains":
                vals = [v if v.startswith("@") else f"@{v}" for v in vals]
            bits.append(f"{words} {', '.join(vals)}")
    return " · ".join(bits) or "No conditions yet"


class SettingsScreen(QWidget):
    def __init__(self, window):
        super().__init__()
        self.win = window
        self.state = window.state
        self._snapshot: dict = {}
        self._learned: dict = {}
        self._rules: list[dict] | None = None
        self._loaded = not self.state.live      # sample mode has no daemon
        self._narrow = False
        self._rows: list[_Row] = []
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._end_flash)
        install_text_size()

        outer = vbox(self, (0, 0, 0, 0), 0)
        self.scroll = ScrollArea()
        outer.addWidget(self.scroll, 1)
        page = QWidget()
        pv = vbox(page, (T.S8, T.S6, T.S8, T.S12), T.S8)
        page.setMaximumWidth(MAX_W)
        # Centered, capped width. The page's stretch dwarfs the spacers so it
        # grows to its cap before they take anything.
        centre = hbox(s=0)
        centre.addStretch(1)
        centre.addWidget(page, 100)
        centre.addStretch(1)
        self.scroll.lay.addLayout(centre)
        self.scroll.lay.addStretch(1)

        self.header = ScreenHeader(
            "Settings", "How Lumen answers, how it looks, and what it's "
                        "connected to.")
        pv.addWidget(self.header)

        def section(title: str, blurb: str):
            box = QWidget()
            v = vbox(box, (0, 0, 0, 0), T.S3)
            v.addWidget(Heading(title, 2))
            v.addWidget(Label(blurb, "muted", wrap=True))
            host = QWidget()
            lay = vbox(host, (0, 0, 0, 0), T.S4)
            v.addWidget(host)
            pv.addWidget(box)
            return box, lay

        self.sec_assistant, self.lay_assistant = section(
            "Assistant", "Where Lumen's answers come from, and what it has "
                         "learned about how you work.")
        self.sec_appearance, self.lay_appearance = section(
            "Appearance", "Colours, text size and motion.")
        self.sec_accounts, self.lay_accounts = section(
            "Accounts", "The services Lumen reads from and, with your OK, "
                        "writes to.")
        self.sec_rules, self.lay_rules = section(
            "Mail rules", "Rules that label new mail automatically.")
        self.sec_advanced, self.lay_advanced = section(
            "Advanced", "The raw configuration, file locations and the "
                        "background service.")

        s = self.state
        s.procedures_changed.connect(self._build_assistant)
        s.model_state_changed.connect(self._on_model_changed)
        sig = getattr(s, "model_switch_highlight_requested", None)
        if sig is not None:
            sig.connect(self._highlight_model)
        self.rebuild()

    # ---- window hooks -------------------------------------------------------
    def on_shown(self):
        s = self.state
        s.fetch_settings(self._on_settings)
        s.fetch_learned(self._on_learned)
        s.refresh_procedures()
        s.list_rules(self._on_rules)

    def refresh(self):
        self.on_shown()

    def apply_theme(self):
        # The theme control mirrors the manager (the sidebar toggle can move it).
        seg = getattr(self, "theme_seg", None)
        if seg is not None:
            try:
                seg.set_value(T.manager().pref)
            except RuntimeError:
                pass

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        narrow = self.width() < NARROW + 2 * T.S8
        if narrow != self._narrow:
            self._narrow = narrow
            for r in list(self._rows):
                try:
                    r.set_narrow(narrow)
                except RuntimeError:
                    self._rows.remove(r)

    # ---- replies ------------------------------------------------------------
    def _on_settings(self, snapshot):
        self._snapshot = snapshot if isinstance(snapshot, dict) else {}
        self._loaded = True
        self._build_assistant()
        self._build_accounts()
        self._build_advanced()

    def _on_learned(self, result):
        self._learned = result if isinstance(result, dict) else {}
        self._build_assistant()

    def _on_rules(self, result):
        self._rules = ((result or {}).get("rules", []) if isinstance(result, dict)
                       else [])
        self._build_rules()

    def _on_model_changed(self):
        self._build_assistant()
        # The reply that moved the switch carries only the model; re-read the
        # snapshot for the Claude details (no loop: an unchanged reply emits
        # nothing).
        self.state.fetch_settings(self._on_settings)

    # ---- build --------------------------------------------------------------
    def rebuild(self):
        self._build_assistant()
        self._build_appearance()
        self._build_accounts()
        self._build_rules()
        self._build_advanced()

    def _row(self, title: str, helper: str = "", title_role: str = "body") -> _Row:
        r = _Row(title, helper, title_role)
        r.set_narrow(self._narrow)
        self._rows.append(r)
        return r

    def _prune_rows(self):
        alive = []
        for r in self._rows:
            try:
                r.objectName()
                alive.append(r)
            except RuntimeError:
                pass
        self._rows = alive

    # ---- assistant ----------------------------------------------------------
    def _mode(self) -> str:
        mode = getattr(self.state, "model_mode", None)
        if mode not in ("off", "local", "claude"):
            model = self._snapshot.get("model") or {}
            mode = model.get("mode") or ("local" if model.get("enabled", True)
                                         else "off")
        return mode if mode in ("off", "local", "claude") else "local"

    def _build_assistant(self):
        lay = self.lay_assistant
        clear_layout(lay)
        self._prune_rows()
        mode = self._mode()

        self.model_card = Card(padding=T.S5, spacing=T.S3)
        self.model_card.setObjectName("modelCard")
        c = self.model_card.lay
        c.addWidget(Heading("Answers come from", 3))
        self.mode_seg = SegmentedControl(
            [("off", "Off"), ("local", "On this computer"), ("claude", "Claude")],
            mode, accessible_name="Where answers come from")
        self.mode_seg.changed.connect(self._set_mode)
        c.addWidget(self.mode_seg, 0, Qt.AlignmentFlag.AlignLeft)
        helper = {
            "off": "Off means the model never loads: no memory used, no fan. "
                   "Mail, calendar, todos, Canvas and search keep working. "
                   "Anything that needs the model says so and points back here.",
            "local": "Answers are generated on this computer. Nothing you ask "
                     "leaves the laptop. The model unloads itself when idle.",
            "claude": "Answers come from Anthropic's Claude. Your question and "
                      "the mail, calendar or Canvas text it needs are sent to "
                      "Anthropic. The local model stays unloaded.",
        }[mode]
        c.addWidget(Label(helper, "muted", wrap=True))

        model = self._snapshot.get("model") or {}
        if mode == "local":
            c.addWidget(Divider())
            r = self._row("Model on this computer")
            r.add(Label(model.get("local_name") or model.get("name")
                        or T.MODEL_NAME, "body"))
            c.addWidget(r)
        elif mode == "claude":
            c.addWidget(Divider())
            self._claude_rows(c, model)
        lay.addWidget(self.model_card)

        # ---- memory ----
        mem = Card(padding=T.S5, spacing=T.S3)
        mem.lay.addWidget(Heading("What Lumen has learned", 3))
        proposed = list(self.state.proposed_procedures or [])
        active = list(self.state.active_procedures or [])
        if proposed:
            mem.lay.addWidget(Label(
                "Lumen noticed these habits. Approve one and Lumen applies it "
                "for you from now on.", "muted", wrap=True))
            for p in proposed:
                mem.lay.addWidget(self._procedure_row(p, True))
        if active:
            if proposed:
                mem.lay.addWidget(Divider())
            mem.lay.addWidget(Eyebrow("In use"))
            for p in active:
                mem.lay.addWidget(self._procedure_row(p, False))
        if not proposed and not active:
            mem.lay.addWidget(Label(
                "No habits yet. As you use Lumen it suggests patterns here for "
                "you to approve.", "muted", wrap=True))
        mem.lay.addWidget(Divider())
        mem.lay.addWidget(Eyebrow("Notes Lumen keeps about you"))
        text = (self._learned.get("text") or "").strip()
        notes = Label(text or "Nothing written down yet.",
                      "small" if text else "muted", wrap=True, selectable=True)
        mem.lay.addWidget(notes)
        foot = hbox(s=T.S2)
        foot.addStretch(1)
        foot.addWidget(Button("Open memory file", "secondary", icon="file",
                              size="sm", on_click=self.state.open_memory_file))
        mem.lay.addLayout(foot)
        lay.addWidget(mem)

    def _claude_rows(self, c, model: dict):
        claude = model.get("claude") or {}
        choices = model.get("claude_models") or {"haiku": "Haiku 4.5",
                                                 "sonnet": "Sonnet 5"}
        current = (model.get("claude_model")
                   or getattr(self.state, "model_claude_model", "haiku"))
        r = self._row("Claude model", "Haiku is faster; Sonnet is more capable.")
        seg = SegmentedControl(list(choices.items()), current,
                               accessible_name="Claude model")
        seg.changed.connect(self._set_claude_model)
        r.add(seg)
        c.addWidget(r)

        installed = claude.get("installed", True)
        logged_in = claude.get("logged_in")
        account = claude.get("account") or ""
        if not installed:
            badge = Badge("warn", "Not installed")
            helper = "Install the Claude command-line tool to use this mode."
        elif logged_in is False:
            badge = Badge("warn", "Signed out")
            helper = "Run  claude auth login  in a terminal, then come back."
        else:
            badge = Badge("success", "Ready")
            helper = f"Signed in as {account}." if account else ""
        r = self._row("Claude app", helper)
        r.add(badge)
        c.addWidget(r)

        usage = claude.get("usage")
        if usage:
            r = self._row("Usage", "How much of your Claude allowance is used.")
            for name, key in (("Last 5 hours", "five_hour"),
                              ("Last 7 days", "seven_day")):
                info = usage.get(key) or {}
                pct = float(info.get("utilization", 0.0) or 0.0)
                grp = QWidget()
                g = vbox(grp, (0, 0, 0, 0), 4)
                top = hbox(s=T.S2)
                top.addWidget(Label(name, "muted"))
                top.addStretch(1)
                top.addWidget(Label(f"{round(pct * 100)}%", "mono"))
                g.addLayout(top)
                g.addWidget(_Meter(pct, 120))
                r.add(grp)
            c.addWidget(r)

    def _procedure_row(self, p: dict, proposed: bool) -> QWidget:
        slug = p.get("slug", "")
        r = self._row(p.get("text") or p.get("summary", ""), title_role="small")
        if proposed:
            r.add(Button("Approve", "secondary", icon="check", size="sm",
                         on_click=lambda: self.state.approve_procedure(slug)))
            r.add(IconButton("x", "Dismiss this suggestion",
                             on_click=lambda: self.state.dismiss_procedure(slug)))
        else:
            r.add(Button("Forget", "ghost", size="sm",
                         on_click=lambda: self.state.remove_procedure(slug)))
        return r

    def _set_mode(self, mode: str) -> None:
        # The daemon's reply drives model_state_changed -> rebuild.
        self.state.set_model_mode(mode)
        self.win.show_toast({"off": "Model turned off and unloaded",
                             "local": "Answers now come from this computer",
                             "claude": "Answers now come from Claude"}
                            .get(mode, "Model setting changed"))

    def _set_claude_model(self, key: str) -> None:
        if key:
            self.state.set_claude_model(key)

    def _highlight_model(self):
        """Arriving from a "model is off" notice: show the control, not just
        the page."""
        QTimer.singleShot(0, self._flash)

    def _flash(self):
        card = getattr(self, "model_card", None)
        if card is None:
            return
        try:
            t = T.current()
            card.setStyleSheet(
                f"QFrame#modelCard {{ background: {t.accent_soft}; "
                f"border: 2px solid {t.accent}; border-radius: {T.R_CARD}px; }}")
            self.scroll.ensureWidgetVisible(card, 0, T.S8)
            self.mode_seg.setFocus()
        except RuntimeError:
            return
        self._flash_timer.start(T.ms(1600) or 1600)

    def _end_flash(self):
        card = getattr(self, "model_card", None)
        try:
            if card is not None:
                card.setStyleSheet("")
        except RuntimeError:
            pass

    # ---- appearance -----------------------------------------------------------
    def _build_appearance(self):
        lay = self.lay_appearance
        clear_layout(lay)
        self._prune_rows()
        card = Card(padding=T.S5, spacing=T.S2)

        r = self._row("Theme", "System follows your computer's light or dark "
                               "setting.")
        self.theme_seg = SegmentedControl(
            [("system", "System"), ("light", "Light"), ("dark", "Dark")],
            T.manager().pref, accessible_name="Theme")
        self.theme_seg.changed.connect(T.manager().set_pref)
        r.add(self.theme_seg)
        card.lay.addWidget(r)
        card.lay.addWidget(Divider())

        r = self._row("Text size", "Makes all text in Lumen larger or smaller.")
        self.size_seg = SegmentedControl(
            [(str(p), f"{p}%") for p in TEXT_SIZES], str(text_size()),
            accessible_name="Text size")
        self.size_seg.changed.connect(self._set_text_size)
        r.add(self.size_seg)
        card.lay.addWidget(r)
        card.lay.addWidget(Divider())

        motion = SwitchRow("Reduce motion",
                           "Turns off sliding and fading animations.",
                           bool(T.settings().value("reduced_motion", False, bool)))
        motion.toggled.connect(self._set_reduced_motion)
        card.lay.addWidget(motion)
        lay.addWidget(card)

    def _set_text_size(self, value: str):
        try:
            pct = int(value)
        except ValueError:
            return
        T.settings().setValue("text_size", pct)
        install_text_size()
        apply_text_size(pct)
        self.win.show_toast(f"Text size set to {pct}%")

    def _set_reduced_motion(self, on: bool):
        T.settings().setValue("reduced_motion", bool(on))
        self.win.show_toast("Animations turned off" if on
                            else "Animations turned on")

    # ---- accounts -------------------------------------------------------------
    def _build_accounts(self):
        lay = self.lay_accounts
        clear_layout(lay)
        self._prune_rows()
        card = Card(padding=T.S5, spacing=T.S2)
        if not self._loaded:
            for _ in range(3):
                card.lay.addWidget(SkeletonRow(2, height=56))
            lay.addWidget(card)
            return
        accounts = self._snapshot.get("accounts") or {}
        gmail = accounts.get("gmail") or {}
        gcal = accounts.get("google_calendar") or {}
        canvas = accounts.get("canvas") or {}
        gmail_on, gcal_on = bool(gmail.get("connected")), bool(gcal.get("connected"))
        need_google = not (gmail_on and gcal_on)
        rows = (
            ("gmail", "Gmail", "Your inbox, labels and sending mail.", gmail,
             need_google and not gmail_on, False),
            ("google_calendar", "Google Calendar",
             "Your events, and new events you approve.", gcal,
             need_google and not gcal_on, False),
            ("canvas", "Canvas", "Assignments and announcements from your "
                                 "courses.", canvas, False, True),
        )
        for i, (key, name, blurb, info, connect, is_canvas) in enumerate(rows):
            if i:
                card.lay.addWidget(Divider())
            card.lay.addWidget(self._account_row(key, name, blurb, info,
                                                 connect, is_canvas))
        if need_google:
            card.lay.addWidget(Label(
                "Gmail and Google Calendar share one Google sign-in, so "
                "connecting one connects both.", "muted", wrap=True))
        lay.addWidget(card)

    def _account_row(self, key, name, blurb, info, connect, is_canvas) -> QWidget:
        connected = bool(info.get("connected"))
        enabled = bool(info.get("enabled", True))
        if connected and not enabled:
            badge = Badge("warn", "Paused")
        elif connected:
            badge = Badge("success", "Connected")
        else:
            badge = Badge("neutral", "Not connected")
        r = self._row(name, blurb)
        r.add(badge)
        refresh = lambda _r=None: self.state.fetch_settings(self._on_settings)
        if connect:
            b = Button("Connect", "secondary", size="sm")
            b.setToolTip("Opens a browser to sign in to Google")
            b.clicked.connect(lambda _=False, btn=b: self._reconnect_google(btn))
            r.add(b)
        elif not connected and is_canvas:
            r.add(Button("Connect in Canvas", "secondary", icon="arrow-right",
                         size="sm",
                         on_click=lambda: self.win.switch_to("canvas")))
        if connected:
            r.add(Button("Resume sync" if not enabled else "Pause sync",
                         "secondary", size="sm",
                         on_click=lambda: self.state.set_connection_enabled(
                             key, not enabled, refresh)))
            r.add(Button("Disconnect", "ghost", size="sm",
                         on_click=lambda: self._confirm_disconnect(key, name,
                                                                   refresh)))
        return r

    def _confirm_disconnect(self, key: str, name: str, refresh):
        payload = {
            "icon": "logout", "title": f"Disconnect {name}?",
            "intro": f"Lumen signs out of {name} and stops syncing it. What's "
                     "already saved on this computer stays. You can connect "
                     "again at any time.",
            "rows": [("Account", name)],
            "confirm_label": "Disconnect", "danger": True,
            "toast": f"{name} disconnected",
        }

        def done(ok, _p):
            if ok:
                self.state.disconnect_connection(key, refresh)
        self.win.confirm.open(payload, done)

    def _reconnect_google(self, btn):
        btn.set_busy(True, "Opening browser…")

        # A failed or abandoned flow comes back on the status channel, not the
        # callback; arm a one-shot rebuild to clear the busy state.
        def recover(_msg=None):
            try:
                self.state.status_requested.disconnect(recover)
            except TypeError:
                pass
            self._build_accounts()

        def done(snapshot):
            try:
                self.state.status_requested.disconnect(recover)
            except TypeError:
                pass
            self._on_settings(snapshot)
            self.win.show_toast("Google connected")

        self.state.status_requested.connect(recover)
        self.state.google_reconnect(done)

    # ---- mail rules -----------------------------------------------------------
    def _build_rules(self):
        lay = self.lay_rules
        clear_layout(lay)
        self._prune_rows()
        card = Card(padding=T.S5, spacing=T.S2)
        if self._rules is None and self.state.live:
            for _ in range(2):
                card.lay.addWidget(SkeletonRow(2, height=56))
            lay.addWidget(card)
            return
        rules = self._rules or []
        if not rules:
            card.lay.addWidget(EmptyState(
                "filter", "No mail rules yet",
                "A rule labels matching mail as it arrives, for example "
                "everything from your landlord.", "New rule",
                lambda: self.state.open_rule_editor()))
            lay.addWidget(card)
            return
        for i, r in enumerate(rules):
            if i:
                card.lay.addWidget(Divider())
            card.lay.addWidget(self._rule_row(r))
        foot = hbox(s=T.S2)
        foot.addStretch(1)
        foot.addWidget(Button("New rule", "secondary", icon="plus", size="sm",
                              on_click=lambda: self.state.open_rule_editor()))
        card.lay.addLayout(foot)
        lay.addWidget(card)

    def _rule_row(self, r: dict) -> QWidget:
        rid = r.get("id")
        on = bool(r.get("enabled", True))
        row = self._row(f"Label as “{r.get('label', '?')}”", _rule_summary(r))
        row.add(Button("Edit", "ghost", icon="edit", size="sm",
                       on_click=lambda: self.state.open_rule_editor(r)))
        sw = Switch(on, f"Rule for {r.get('label', '')}")
        sw.toggled.connect(lambda v: self.state.toggle_rule(rid, v, self._on_rules))
        row.add(sw)
        row.add(IconButton("trash", "Delete rule",
                           on_click=lambda: self._delete_rule(r)))
        return row

    def _delete_rule(self, r: dict):
        rid = r.get("id")
        name = r.get("label", "")
        self.state.delete_rule(rid, self._on_rules)
        self.win.show_toast(f"Deleted the rule for “{name}”")

    # ---- advanced -------------------------------------------------------------
    def _build_advanced(self):
        lay = self.lay_advanced
        clear_layout(lay)
        self._prune_rows()
        if not self._loaded:
            card = Card(padding=T.S5, spacing=T.S2)
            for _ in range(3):
                card.lay.addWidget(SkeletonRow(2, height=48))
            lay.addWidget(card)
            return
        snap = self._snapshot

        # Paths
        paths = Card(padding=T.S5, spacing=T.S2)
        paths.lay.addWidget(Heading("Files", 3))
        r = self._row("Config file", "Edit it here or in your editor; Lumen "
                                     "reloads it when saved.")
        cfg = snap.get("config_path", "~/.config/lumen/config.toml")
        r.add(Label(cfg, "small", selectable=True))
        paths.lay.addWidget(r)
        paths.lay.addWidget(Divider())
        try:
            from lumen.daemon.config import default_memory_path
            mem_path = str(default_memory_path())
        except Exception:
            mem_path = self._learned.get("path") or ""
        r = self._row("Memory file", "The notes Lumen keeps about you, as "
                                     "plain text you can edit.")
        r.add(Label(mem_path, "small", selectable=True))
        r.add(Button("Open", "secondary", size="sm",
                     on_click=self.state.open_memory_file))
        paths.lay.addWidget(r)
        lay.addWidget(paths)

        # Raw config
        raw = Card(padding=T.S5, spacing=T.S3)
        raw.lay.addWidget(Heading("Configuration", 3))
        raw.lay.addWidget(Label("Read-only view of the values Lumen is running "
                                "with.", "muted", wrap=True))
        raw.lay.addWidget(self._toml_view())
        lay.addWidget(raw)

        # MCP servers
        mcp = Card(padding=T.S5, spacing=T.S2)
        mcp.lay.addWidget(Heading("Connected tools (MCP servers)", 3))
        servers = snap.get("mcp_servers") or {}
        if not servers:
            mcp.lay.addWidget(Label("No servers configured in [mcp_servers].",
                                    "muted", wrap=True))
        for i, (name, cfg_) in enumerate(servers.items()):
            if i:
                mcp.lay.addWidget(Divider())
            cfg_ = cfg_ or {}
            desc = cfg_.get("description") or cfg_.get("transport") or "Not configured"
            r = self._row(name, desc)
            on = bool(cfg_.get("enabled"))
            r.add(Badge("success" if on else "neutral", "On" if on else "Off"))
            mcp.lay.addWidget(r)
        lay.addWidget(mcp)

        # Background service
        bg = Card(padding=T.S5, spacing=T.S2)
        bg.lay.addWidget(Heading("Background sync", 3))
        try:
            from lumen import instance_lock
            running = len(instance_lock.daemon_pids())
        except Exception:
            running = -1
        if running < 0:
            badge = Badge("warn", "Couldn't check")
        elif running == 0:
            badge = Badge("neutral", "Not running")
        else:
            badge = Badge("success", "Running" if running == 1
                          else f"{running} running")
        r = self._row("Background service",
                      "Keeps mail, calendar and Canvas in sync while the window "
                      "is closed. Stopping it loses nothing; Lumen starts it "
                      "again next time you open the app.")
        r.add(badge)
        r.add(Button("Stop", "secondary", size="sm", on_click=self._stop_daemons))
        bg.lay.addWidget(r)
        bg.lay.addWidget(Divider())
        r = self._row("Quit Lumen", "Closes the window and stops background "
                                    "sync with it.")
        r.add(Button("Quit Lumen", "danger", size="sm",
                     on_click=self._quit_everything))
        bg.lay.addWidget(r)
        lay.addWidget(bg)

    def _toml_view(self) -> QWidget:
        snap = self._snapshot
        model = snap.get("model") or {}
        sync = snap.get("sync") or {}

        def val(v):
            if isinstance(v, bool):
                return "true" if v else "false"
            if isinstance(v, (int, float)):
                return str(v)
            return f'"{v}"'

        lines = ["[model]",
                 f"mode = {val(self._mode())}",
                 f"runtime = {val(model.get('runtime', 'ollama'))}",
                 f"context = {val(model.get('context', 8192))}",
                 f"idle_timeout = {val(model.get('idle_timeout', 600))}"
                 "   # seconds before the model unloads",
                 "",
                 "[sync]",
                 f"interval = {val(sync.get('interval', 5))}   # minutes",
                 f"on_wake = {val(bool(sync.get('on_wake', True)))}",
                 f"confirm_writes = {val(bool(sync.get('confirm_writes', True)))}",
                 "",
                 "[mail]",
                 f"load_remote_images = {val(bool(self.state.load_remote_images))}",
                 "",
                 "# ui_v4 preferences (QSettings lumen/ui_v4)",
                 f"theme = {val(T.manager().pref)}",
                 f"text_size = {text_size()}",
                 f"reduced_motion = {val(T.reduced_motion())}"]
        view = QPlainTextEdit("\n".join(lines))
        view.setProperty("role", "code")
        view.setReadOnly(True)
        view.setAccessibleName("Current configuration")
        view.setMinimumHeight(20 * len(lines) + 24)
        view.setMaximumHeight(20 * len(lines) + 40)
        return view

    def _stop_daemons(self):
        from lumen import instance_lock
        pids = instance_lock.daemon_pids()
        if not pids:
            self.win.show_toast("Background sync wasn't running")
        else:
            instance_lock.terminate(pids)
            self.win.show_toast("Stopped background sync")
        self._build_advanced()

    def _quit_everything(self):
        from lumen import instance_lock
        instance_lock.terminate(instance_lock.daemon_pids())
        QApplication.instance().quit()
