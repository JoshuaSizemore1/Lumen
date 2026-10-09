"""LumenWindow — the Field Notes shell: 232px sidebar (64px rail below 900px),
lazily-built screen stack, the Ask bar, and the window-level overlays.

Screens live in lumen/ui_v4/screens/<key>.py as `<Key>Screen(window)`. They
are imported and built the first time they are shown; one that fails to
import or construct shows an error state instead of taking the app down.
"""
import importlib
import inspect
import sys
import traceback
from datetime import date

from PyQt6.QtCore import QEvent, QRectF, Qt
from PyQt6.QtGui import QKeySequence, QPainter, QPen, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QFrame, QLabel, QLineEdit, QStackedWidget, QWidget,
)

from ..ui_v3.nav_history import NavController, NavEntry
from ..ui_v3.state import AppState
from ..ui_v3.swipe_nav import BACK, SwipeNavigator
from . import icons
from . import theme as T
from .components import (
    Badge, Divider, Dot, ElideLabel, EmptyState, Eyebrow, IconButton, IconLabel,
    Label, fire_on_next_tick, hbox, set_prop, vbox,
)
from .overlays import (
    CommandPalette, ComposeOverlay, ConfirmOverlay, EventOverlay, RuleDialog,
    SuggestReviewOverlay, Toast,
)

# (key, label, icon, shortcut) — stack order; Ctrl+1..8 in this order.
NAV = (("today", "Today", "today", "Ctrl+1"),
       ("mail", "Inbox", "inbox", "Ctrl+2"),
       ("calendar", "Calendar", "calendar", "Ctrl+3"),
       ("todos", "Todos", "todos", "Ctrl+4"),
       ("books", "Books", "books", "Ctrl+5"),
       ("files", "Files", "files", "Ctrl+6"),
       ("canvas", "Canvas", "canvas", "Ctrl+7"),
       ("ask", "Ask Lumen", "ask", "Ctrl+8"))
SETTINGS = ("settings", "Settings", "settings", "Ctrl+,")
SCREENS = tuple(n[0] for n in NAV) + ("settings",)
TITLES = {k: t for k, t, _i, _s in NAV + (SETTINGS,)}
LEGACY = {"chat": "ask", "dashboard": "today"}
NO_ASKBAR = ("ask", "settings")
THEME_PREFS = ("system", "light", "dark")
THEME_ICON = {"system": "contrast", "light": "sun", "dark": "moon"}


def _log_exc(where: str) -> None:
    print(f"[lumen.ui_v4] {where}:", file=sys.stderr)
    traceback.print_exc()


# ---- sidebar ----------------------------------------------------------------
class NavItem(QFrame):
    """Sidebar row: icon + label + right-aligned count. Selected = accent_soft."""

    def __init__(self, key: str, text: str, icon: str, shortcut: str, on_click):
        super().__init__()
        self.key, self._text, self._shortcut = key, text, shortcut
        self._on_click = on_click
        self._count = 0
        self._badge_mode = False
        self.setProperty("role", "nav")
        self.setProperty("selected", False)
        self.setFixedHeight(40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(text)
        self._lay = hbox(self, (12, 0, 12, 0), 12)
        self.icon = IconLabel(icon, "fg2")
        self._lay.addWidget(self.icon)
        self.label = QLabel(text)
        self.label.setProperty("role", "nav-label")
        self._lay.addWidget(self.label, 1)
        self.count = QLabel("")
        self.count.setProperty("role", "nav-count")
        self.count.hide()
        self._lay.addWidget(self.count)
        self.badge = Badge("count", "")
        self.badge.hide()
        self._lay.addWidget(self.badge)
        self._collapsed = False
        self._tip()

    def _tip(self):
        extra = f" — {self._count}" if self._count else ""
        self.setToolTip(f"{self._text}{extra}" + (f" ({self._shortcut})"
                                                  if self._shortcut else ""))

    def set_text(self, text: str) -> None:
        self._text = text
        self.label.setText(text)
        self.setAccessibleName(text)
        self._tip()

    def set_icon(self, name: str) -> None:
        self.icon.set_icon(name)

    def set_selected(self, on: bool) -> None:
        set_prop(self, "selected", on)
        self.label.style().unpolish(self.label)
        self.label.style().polish(self.label)
        self.icon.set_icon(color="accent_soft_fg" if on else "fg2")

    def set_count(self, n: int, badge: bool = False) -> None:
        self._count = n
        self._badge_mode = badge
        target, other = (self.badge, self.count) if badge else (self.count, self.badge)
        target.setText(str(n))
        target.setVisible(bool(n) and not self._collapsed)
        other.hide()
        self._tip()

    def set_collapsed(self, on: bool) -> None:
        self._collapsed = on
        self.label.setVisible(not on)
        (self.badge if self._badge_mode else self.count).setVisible(bool(self._count) and not on)
        self._lay.setContentsMargins(14 if on else 12, 0, 14 if on else 12, 0)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            fire_on_next_tick(self._on_click)
        super().mousePressEvent(ev)

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            fire_on_next_tick(self._on_click)
        else:
            super().keyPressEvent(ev)


# ---- ask bar ----------------------------------------------------------------
class AskBar(QFrame):
    """Bottom of the content area. Hands the question — with the current
    page's context — to the Ask screen's one conversation."""

    def __init__(self, window):
        super().__init__()
        self.win = window
        self.setObjectName("askbar")
        h = hbox(self, (T.S6, T.S3, T.S6, T.S3), T.S3)
        self.label = QLabel("Ask Lumen")
        self.label.setObjectName("askLabel")
        h.addWidget(self.label)
        self.field = QFrame()
        self.field.setObjectName("askField")
        fh = hbox(self.field, (T.S3, 4, 4, 4), T.S2)
        fh.addWidget(IconLabel("ask", "muted", T.ICON_SM))
        self.input = QLineEdit()
        self.input.setProperty("role", "bare")
        self.input.setPlaceholderText("Ask about this page…")
        self.input.setAccessibleName("Ask Lumen")
        self.input.returnPressed.connect(self.submit)
        self.input.installEventFilter(self)
        self.label.setBuddy(self.input)
        fh.addWidget(self.input, 1)
        self.send = IconButton("arrow-up", "Ask", size=32, variant="primary",
                               on_click=self.submit)
        fh.addWidget(self.send)
        h.addWidget(self.field, 1)

    def eventFilter(self, obj, ev):
        if obj is self.input and ev.type() in (QEvent.Type.FocusIn,
                                              QEvent.Type.FocusOut):
            set_prop(self.field, "focused", ev.type() == QEvent.Type.FocusIn)
        return False

    def focus(self):
        self.input.setFocus()
        self.input.selectAll()

    def submit(self):
        q = self.input.text().strip()
        if not q:
            return
        self.input.clear()
        self.win.ask(q, self.win.current_ask_context())

    def set_collapsed(self, on: bool) -> None:
        self.label.setVisible(not on)


# ---- swipe indicator --------------------------------------------------------
class _SwipeIndicator(QWidget):
    """Circle + arrow that drags in from an edge during a back/forward swipe.
    Painting only; SwipeNavigator (ui_v3) owns the logic."""

    D, PAD, INSET = 40, 8, 24

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFixedSize(self.D + 2 * self.PAD, self.D + 2 * self.PAD)
        self._side, self._p, self._capped = BACK, 0.0, False
        self.hide()

    def set_progress(self, side: str, p: float, capped: bool) -> None:
        self._side, self._p, self._capped = side, max(0.0, min(1.0, p)), capped
        par = self.parentWidget()
        if par is not None:
            reach = self._p * (0.55 if capped else 1.0)
            travel = self.INSET + self.width()
            x = (-self.width() + reach * travel if side == BACK
                 else par.width() - reach * travel)
            self.move(int(x), (par.height() - self.height()) // 2)
        self.show()
        self.raise_()
        self.update()

    def dismiss(self) -> None:
        self._p = 0.0
        self.hide()

    def paintEvent(self, ev):
        t = T.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity((0.35 + 0.65 * self._p) * (0.5 if self._capped else 1.0))
        p.setPen(QPen(t.color("border_strong"), 1))
        p.setBrush(t.color("surface"))
        p.drawEllipse(QRectF(self.PAD, self.PAD, self.D, self.D))
        pm = icons.pixmap("arrow-left" if self._side == BACK else "arrow-right",
                          "accent", T.ICON)
        c = (self.width() - T.ICON) // 2
        p.drawPixmap(c, c, pm)


# ---- window -----------------------------------------------------------------
class LumenWindow(QWidget):
    quit_on_close = False
    NAV_ENTRIES = NAV + (SETTINGS,)

    def __init__(self, state: AppState | None = None, chat_client=None):
        super().__init__()
        self.setObjectName("root")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setWindowTitle("Lumen")
        self.setMinimumSize(T.MIN_W, T.MIN_H)
        self.resize(T.WINDOW_W, T.WINDOW_H)

        self.state = state or AppState()
        self.chat_client = chat_client
        self._settings = T.settings()
        self._collapsed = False
        self._current_key: str | None = None
        self._screen_cache: dict[str, QWidget] = {}

        root = hbox(self, (0, 0, 0, 0), 0)
        self.sidebar = self._build_sidebar()
        root.addWidget(self.sidebar)

        self.content = QWidget()
        self.content.setObjectName("content")
        self.content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        cv = vbox(self.content, (0, 0, 0, 0), 0)
        self.stack = QStackedWidget()
        self.stack.setObjectName("stack")
        for _key in SCREENS:
            self.stack.addWidget(QWidget())       # placeholder, swapped on build
        cv.addWidget(self.stack, 1)
        self.askbar = AskBar(self)
        cv.addWidget(self.askbar)
        root.addWidget(self.content, 1)

        # ---- back/forward history (shared with in-page steps) -------------
        self._restoring = False
        self.nav = NavController()
        self._swipe_indicator = _SwipeIndicator(self.content)
        self._swipe = SwipeNavigator(self.nav, self._restore,
                                     self._swipe_indicator, within=self.content)
        QApplication.instance().installEventFilter(self._swipe)

        # ---- overlays -----------------------------------------------------
        self.confirm = ConfirmOverlay(self)
        self.compose = ComposeOverlay(self, self.state)
        self.event = EventOverlay(self, self.state)
        self.rules = RuleDialog(self, self.state)
        self.suggest_review = SuggestReviewOverlay(self, self.state)
        self.command_palette = CommandPalette(self)
        self.toast = Toast(self)
        self._modals = (self.confirm, self.compose, self.event, self.rules,
                        self.suggest_review, self.command_palette)

        # ---- state wiring -------------------------------------------------
        s = self.state
        s.view_requested.connect(self.switch_to)
        s.toast_requested.connect(self.show_toast)
        s.status_requested.connect(self.show_toast)
        s.confirm_requested.connect(self._open_confirm)
        s.compose_requested.connect(self._open_compose)
        s.rule_edit_requested.connect(self._open_rules)
        s.open_chat_requested.connect(self._open_chat)
        s.model_state_changed.connect(self._sync_model)
        s.todos_changed.connect(self._update_counts)
        s.mails_changed.connect(self._update_counts)
        for name, slot in (("event_compose_requested", self.open_event),
                           ("suggest_review_requested", self._open_suggest_review),
                           ("open_file_requested", self._open_file),
                           ("nav_location_changed", self._record_location)):
            sig = getattr(s, name, None)
            if sig is not None:
                sig.connect(slot)

        # ---- shortcuts ----------------------------------------------------
        for key, _t, _i, seq in self.NAV_ENTRIES:
            QShortcut(QKeySequence(seq), self,
                      activated=lambda k=key: self._shortcut_nav(k))
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self._open_palette)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self._focus_ask)
        QShortcut(QKeySequence("Alt+Left"), self, activated=self.go_back)
        QShortcut(QKeySequence("Alt+Right"), self, activated=self.go_forward)

        T.manager().changed.connect(self._on_theme)
        self.stack.currentChanged.connect(self._sync_chrome)
        QApplication.instance().aboutToQuit.connect(self._save_geometry)
        self._update_counts()
        self._sync_model()
        self._sync_theme_item()

        geo = self._settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        start = self._settings.value("last_screen", "today", str)
        self.switch_to(start if start in SCREENS else "today")

    # ---- public API ---------------------------------------------------------
    @property
    def theme(self) -> T.Theme:
        return T.current()

    def switch_to(self, key: str, **kw) -> None:
        key = LEGACY.get(key, key)
        if key not in SCREENS:
            return
        screen = self._screen(key)
        idx = SCREENS.index(key)
        if self.stack.currentIndex() != idx:
            self.stack.setCurrentIndex(idx)
        if self._current_key != key:
            # A lazy build can leave the stack already on idx (no signal).
            self._sync_chrome(idx)
        self._call_shown(screen, kw)

    def show_toast(self, msg: str, undo=None) -> None:
        self._toast_inset()
        self.toast.pop(msg, undo)

    def ask(self, question: str, context=None) -> None:
        """Hand a question to the Ask screen's conversation."""
        question = (question or "").strip()
        if not question:
            return
        self.switch_to("ask")
        start = getattr(self._screen_cache.get("ask"), "start", None)
        if start is None:
            self.show_toast("Ask Lumen isn't available right now.")
            return
        self._safe("ask.start", start, question, context)

    def current_ask_context(self):
        """The visible screen's ask_context(), or None."""
        fn = getattr(self._screen_cache.get(self._current_key), "ask_context", None)
        return self._safe("ask_context", fn) if fn is not None else None

    def open_event(self, payload: dict | None = None) -> None:
        payload = dict(payload or {})
        self._present()
        if not payload.get("date"):
            payload["date"] = date.today().isoformat()
        self.event.open(payload)

    def cycle_theme(self) -> None:
        """system -> light -> dark -> system; persisted by the manager."""
        pref = T.manager().pref
        nxt = THEME_PREFS[(THEME_PREFS.index(pref) + 1) % len(THEME_PREFS)]
        T.manager().set_pref(nxt)

    def go_back(self) -> None:
        entry = self.nav.back()
        if entry is not None:
            self._restore(entry)

    def go_forward(self) -> None:
        entry = self.nav.forward()
        if entry is not None:
            self._restore(entry)

    def current_key(self) -> str | None:
        return self._current_key

    # ---- sidebar ------------------------------------------------------------
    def _build_sidebar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("sidebar")
        bar.setFixedWidth(T.SIDEBAR_W)
        v = vbox(bar, (T.S3, T.S5, T.S3, T.S4), 2)

        brand = QWidget()
        bv = vbox(brand, (12, 0, 12, T.S5), 2)
        self.brand = QLabel("Lumen")
        self.brand.setObjectName("brand")
        bv.addWidget(self.brand)
        self.brand_caption = QLabel("Local assistant")
        self.brand_caption.setObjectName("brandCaption")
        bv.addWidget(self.brand_caption)
        v.addWidget(brand)

        self.nav_items: dict[str, NavItem] = {}
        self._eyebrows: list[QWidget] = []
        by_key = {n[0]: n for n in NAV}

        def item(key):
            k, text, icon, seq = by_key[key]
            it = NavItem(k, text, icon, seq, lambda: self.switch_to(k))
            self.nav_items[k] = it
            v.addWidget(it)

        def eyebrow(text):
            e = Eyebrow(text, "nav-eyebrow")
            e.setContentsMargins(12, T.S4, 12, T.S1)
            self._eyebrows.append(e)
            v.addWidget(e)

        item("today")
        eyebrow("Daily")
        for k in ("mail", "calendar", "todos"):
            item(k)
        eyebrow("Library")
        for k in ("books", "files", "canvas"):
            item(k)
        v.addSpacing(T.S4)
        item("ask")
        v.addStretch(1)

        k, text, icon, seq = SETTINGS
        self.nav_items[k] = NavItem(k, text, icon, seq,
                                    lambda: self.switch_to("settings"))
        v.addWidget(self.nav_items[k])
        self.theme_item = NavItem("theme", "Theme", "contrast", "",
                                  self.cycle_theme)
        v.addWidget(self.theme_item)
        v.addSpacing(T.S2)
        v.addWidget(Divider())
        v.addSpacing(T.S2)

        self.status_line = QFrame()
        self.status_line.setObjectName("statusLine")
        sl = hbox(self.status_line, (12, 4, 12, 0), T.S2)
        self.model_dot = Dot("success", 8)
        sl.addWidget(self.model_dot)
        self.model_label = ElideLabel("", "caption")
        sl.addWidget(self.model_label, 1)
        v.addWidget(self.status_line)
        self.date_label = Label("", "caption")
        self.date_label.setContentsMargins(12, 2, 12, 0)
        v.addWidget(self.date_label)
        self._sync_date()
        return bar

    def _set_collapsed(self, on: bool) -> None:
        if on == self._collapsed:
            return
        self._collapsed = on
        self.sidebar.setFixedWidth(T.RAIL_W if on else T.SIDEBAR_W)
        self.sidebar.layout().setContentsMargins(
            T.S2 if on else T.S3, T.S5, T.S2 if on else T.S3, T.S4)
        self.brand.setText("L" if on else "Lumen")
        self.brand_caption.setVisible(not on)
        for e in self._eyebrows:
            e.setVisible(not on)
        for it in list(self.nav_items.values()) + [self.theme_item]:
            it.set_collapsed(on)
        self.model_label.setVisible(not on)
        self.date_label.setVisible(not on)
        self.status_line.layout().setContentsMargins(20 if on else 12, 4, 0, 0)
        self.askbar.set_collapsed(on)

    def _sync_date(self):
        d = date.today()
        self.date_label.setText(d.strftime("%A, %B ") + str(d.day))

    def _sync_model(self):
        st = self.state
        mode = getattr(st, "model_mode", "local")
        if not st.model_enabled or mode == "off":
            text, color = "Model off", "muted"
        elif mode == "claude":
            sub = getattr(st, "model_claude_model", "") or ""
            text, color = f"Claude · {sub.capitalize()}" if sub else "Claude", "info"
        else:
            text, color = f"Local · {T.MODEL_NAME}", "success"
        self.model_dot.set_color(color)
        self.model_label.setText(text)
        self.status_line.setToolTip(f"Model: {text}")

    def _sync_theme_item(self):
        pref = T.manager().pref
        nxt = THEME_PREFS[(THEME_PREFS.index(pref) + 1) % len(THEME_PREFS)]
        label = {"system": "Theme: system", "light": "Theme: light",
                 "dark": "Theme: dark"}[pref]
        self.theme_item.set_text(label)
        self.theme_item.set_icon(THEME_ICON[pref])
        tip = f"{label}. Switch to {nxt}."
        self.theme_item.setToolTip(tip)
        self.theme_item.setAccessibleName(tip)

    def _update_counts(self):
        self.nav_items["mail"].set_count(self.state.unread_count(), badge=True)
        self.nav_items["todos"].set_count(self.state.open_count())

    # ---- lazy screens -------------------------------------------------------
    def screen(self, key: str) -> QWidget | None:
        """A built screen, or None — never builds."""
        return self._screen_cache.get(LEGACY.get(key, key))

    def _screen(self, key: str) -> QWidget:
        screen = self._screen_cache.get(key)
        if screen is not None:
            return screen
        screen = self._make_screen(key)
        self._screen_cache[key] = screen
        self._swap_in(key, screen)
        return screen

    def _swap_in(self, key: str, widget: QWidget) -> None:
        idx = SCREENS.index(key)
        old = self.stack.widget(idx)
        # Signals blocked: removeWidget renumbers the stack and would emit
        # currentChanged for pure bookkeeping (ui_v3's build cascade).
        was = self.stack.blockSignals(True)
        try:
            self.stack.insertWidget(idx, widget)
            self.stack.removeWidget(old)
            # Removing the current placeholder moves the stack elsewhere; put
            # it back on whatever the user was looking at.
            if self._current_key in SCREENS:
                self.stack.setCurrentIndex(SCREENS.index(self._current_key))
        finally:
            self.stack.blockSignals(was)
        old.deleteLater()

    def _make_screen(self, key: str) -> QWidget:
        try:
            mod = importlib.import_module(f"lumen.ui_v4.screens.{key}")
            return getattr(mod, f"{key.capitalize()}Screen")(self)
        except Exception as e:
            _log_exc(f"building screen {key!r}")
            return EmptyState(
                "alert", f"{TITLES[key]} couldn't load",
                f"{type(e).__name__}: {e}", "Try again",
                lambda k=key: self._retry(k))

    def _retry(self, key: str) -> None:
        self._screen_cache.pop(key, None)
        self._swap_in(key, QWidget())
        self._current_key = None if self._current_key == key else self._current_key
        self.switch_to(key)

    def _call_shown(self, screen, kw: dict) -> None:
        fn = getattr(screen, "on_shown", None)
        if fn is None:
            return
        if kw:
            try:
                params = inspect.signature(fn).parameters
                if not any(p.kind is p.VAR_KEYWORD for p in params.values()):
                    kw = {k: v for k, v in kw.items() if k in params}
            except (TypeError, ValueError):
                kw = {}
        self._safe("on_shown", fn, **kw)

    def _safe(self, where: str, fn, *a, **kw):
        """Call into a screen without letting its exception abort the app."""
        try:
            return fn(*a, **kw)
        except Exception:
            _log_exc(where)
            self.toast.pop("Something went wrong — details are in the log.")
            return None

    # ---- chrome -------------------------------------------------------------
    def _sync_chrome(self, index: int):
        if not 0 <= index < len(SCREENS):
            return
        key = SCREENS[index]
        self._current_key = key
        self._record_location()
        for k, it in self.nav_items.items():
            it.set_selected(k == key)
        self.askbar.setVisible(key not in NO_ASKBAR)
        self._toast_inset()
        self._sync_date()
        self._settings.setValue("last_screen", key)
        if key == "ask":
            self.state.warm_model()

    def _toast_inset(self):
        self.toast.bottom_inset = (self.askbar.sizeHint().height() + T.S4
                                   if self.askbar.isVisibleTo(self) else T.S6)
        if self.toast.isVisible():
            self.toast.reposition()

    def _modal_open(self) -> bool:
        return any(m.isVisible() for m in self._modals)

    def _shortcut_nav(self, key: str):
        if not self._modal_open():
            self.switch_to(key)

    def _open_palette(self):
        if not self._modal_open():
            self.command_palette.open()

    def _focus_ask(self):
        if self._modal_open():
            return
        if self.askbar.isVisible():
            self.askbar.focus()
        else:
            self.switch_to("ask", focus=True)

    def _on_theme(self, _theme):
        self._sync_theme_item()
        self._sync_model()
        self._swipe_indicator.update()
        for key, scr in list(self._screen_cache.items()):
            fn = getattr(scr, "apply_theme", None)
            if fn is not None:
                self._safe(f"{key}.apply_theme", fn)

    # ---- history ------------------------------------------------------------
    def _current_entry(self) -> NavEntry:
        key = self._current_key
        scr = self._screen_cache.get(key)
        fn = getattr(scr, "nav_token", None)
        return NavEntry(key, self._safe("nav_token", fn) if fn else None)

    def _record_location(self):
        if self._restoring or self._current_key is None:
            return
        self.nav.visit(self._current_entry())

    def _restore(self, entry: NavEntry):
        self._restoring = True
        try:
            self.switch_to(entry.screen)
            fn = getattr(self._screen_cache.get(entry.screen), "nav_restore", None)
            if entry.token is not None and fn is not None:
                self._safe("nav_restore", fn, entry.token)
        finally:
            self._restoring = False

    # ---- state-driven overlays ---------------------------------------------
    def _present(self):
        # An overlay can be asked for while only the hotkey launcher is up.
        self.show()
        self.raise_()
        self.activateWindow()

    def _open_confirm(self, payload: dict):
        self._present()
        self.confirm.open(payload, self._on_confirm_result)

    def _on_confirm_result(self, approved: bool, payload: dict):
        confirm_id = payload.get("confirm_id")
        if confirm_id is not None:     # daemon confirm-over-IPC: always answer
            self.state.respond_confirm(confirm_id, approved,
                                       check=payload.get("check_state"))
        if approved and payload.get("toast"):
            self.show_toast(payload["toast"])

    def _open_compose(self, payload: dict):
        self._present()
        self.compose.open(payload)

    def _open_rules(self, prefill: dict):
        self._present()
        self.rules.open(prefill)

    def _open_suggest_review(self):
        self._present()
        self.suggest_review.open()

    def _open_chat(self, conv_id: int):
        self._present()
        self.switch_to("ask")
        scr = self._screen_cache.get("ask")
        fn = getattr(scr, "open_conversation", None) or getattr(
            scr, "load_conversation", None)
        if fn is not None:
            self._safe("ask.open_conversation", fn, conv_id)

    def _open_file(self, path: str):
        self.switch_to("files")
        fn = getattr(self._screen_cache.get("files"), "open_path", None)
        if fn is not None:
            self._safe("files.open_path", fn, path)

    # ---- geometry -----------------------------------------------------------
    def _save_geometry(self):
        self._settings.setValue("geometry", self.saveGeometry())

    def closeEvent(self, ev):
        self._save_geometry()
        super().closeEvent(ev)
        if self.quit_on_close:
            QApplication.instance().quit()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._set_collapsed(self.width() < T.COLLAPSE_BELOW)
        self._toast_inset()


def build_window(state: AppState | None = None, chat_client=None) -> LumenWindow:
    """Factory for app.main() and screenshot scripts (sample mode when state
    is None). Needs a QApplication; applies fonts + theme first. chat_client
    goes to the constructor because the start screen is built inside it."""
    app = QApplication.instance()
    T.load_fonts()
    T.manager().apply(app)
    return LumenWindow(state, chat_client)
