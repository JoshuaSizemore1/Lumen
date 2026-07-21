"""Main window: 220px sidebar + stacked screens + the Ask Lumen bar.

The mock's sidebar carried a "Quick launch ⌘K" row above Settings. That row is
deliberately absent — quick launch is the Super+L overlay in `launcher.py`, and
a duplicate entry point in the chrome would only muddy where it lives.
"""
import sys
from datetime import date

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QFrame, QStackedWidget, QStyleFactory, QWidget,
)

from . import theme as T
from .askbar import ASK_SCREENS, AskBar
from .components import NavRow
from .overlays import ComposeOverlay, ConfirmOverlay, EventOverlay, Toast
from .rule_dialog import RuleDialog
from .state import AppState
from .styles import app_palette, build_qss
from .widgets import ClickRow, Dot, hbox, hline, label, qcolor, vbox
from .screens.books import BooksScreen
from .screens.calendar import CalendarScreen
from .screens.chat import ChatScreen
from .screens.files import FilesScreen
from .screens.mail import MailScreen
from .screens.settings import SettingsScreen
from .screens.today import TodayScreen
from .screens.todos import TodosScreen

# Order is the stack order and the 1-7 shortcut order.
NAV = (("today", "Today", "1"), ("calendar", "Calendar", "2"),
       ("mail", "Mail", "3"), ("todos", "Todos", "4"),
       ("books", "Books", "5"), ("chat", "Chat", "6"),
       ("files", "Files", "7"))
SCREENS = tuple(k for k, _, _ in NAV) + ("settings",)

_active_window = None   # keeps the rebuilt window alive across an accent switch


def _settings() -> QSettings:
    return QSettings("lumen", "ui_v3")


class LumenWindow(QWidget):
    quit_on_close = False

    def __init__(self, state: AppState | None = None, chat_client=None):
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle("Lumen — local assistant")
        self.resize(T.WINDOW_W, T.WINDOW_H)
        self.setMinimumSize(T.MIN_W, T.MIN_H)

        self.state = state or AppState()
        self.chat_client = chat_client

        root = hbox(self, (0, 0, 0, 0), 0)
        root.addWidget(self._sidebar())

        main = QWidget()
        main.setObjectName("main")
        mv = vbox(main, (0, 0, 0, 0), 0)

        self.stack = QStackedWidget()
        self.screens = {
            "today": TodayScreen(self.state),
            "calendar": CalendarScreen(self.state),
            "mail": MailScreen(self.state),
            "todos": TodosScreen(self.state),
            "books": BooksScreen(self.state),
            "chat": ChatScreen(self.state, chat_client),
            "files": FilesScreen(self.state),
            "settings": SettingsScreen(self.state),
        }
        for key in SCREENS:
            self.stack.addWidget(self.screens[key])
        mv.addWidget(self.stack, 1)      # greedy: screens absorb the growth

        self.askbar = AskBar(self.state, chat_client, self._ask_context)
        mv.addWidget(self.askbar)
        root.addWidget(main, 1)

        # ---- overlays -----------------------------------------------------
        self.confirm = ConfirmOverlay(self)
        self.compose = ComposeOverlay(self, self.state)
        self.event = EventOverlay(self, self.state)
        self.rules = RuleDialog(self, self.state)
        self.toast = Toast(self)

        self.state.view_requested.connect(self.switch_to)
        self.state.open_chat_requested.connect(self._open_chat)
        self.state.confirm_requested.connect(self._open_confirm)
        self.state.compose_requested.connect(self._open_compose)
        self.state.event_compose_requested.connect(self._open_event)
        self.state.rule_edit_requested.connect(self._open_rules)
        self.state.toast_requested.connect(self.toast.pop)
        self.state.status_requested.connect(self.toast.pop)
        self.state.mails_changed.connect(self._update_badges)
        self.state.accent_requested.connect(self._change_accent)
        self.state.font_scale_requested.connect(self._change_font_scale)

        for i, (key, _, _) in enumerate(NAV):
            sc = QShortcut(QKeySequence(str(i + 1)), self)
            sc.activated.connect(lambda k=key: self.switch_to(k))
        QShortcut(QKeySequence("Ctrl+,"), self,
                  activated=lambda: self.switch_to("settings"))
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self.askbar.focus)

        # Driven off the stack rather than switch_to, so the chrome stays
        # correct however the page changed (nav, shortcut, or a direct
        # setCurrentIndex from a tool).
        self.stack.currentChanged.connect(self._sync_chrome)
        self._update_badges()
        # The stack already sits on index 0, so setCurrentIndex(0) emits nothing
        # and the first screen would open with no nav highlight. Sync directly.
        self.stack.setCurrentIndex(0)
        self._sync_chrome(0)

    # ---- sidebar ----------------------------------------------------------
    def _sidebar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("sidebar")
        bar.setFixedWidth(T.SIDEBAR_W)
        v = vbox(bar, (0, 26, 0, 18), 0)

        brand = hbox(m=(22, 0, 22, 18), s=10)
        mark = label("L", 13, T.ACCENT_ON, 700)
        mark.setFixedSize(T.sc(22), T.sc(22))
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setStyleSheet(f"background: {T.ACCENT}; border-radius: 5px;")
        brand.addWidget(mark)
        name = vbox(s=3)
        name.addWidget(label("Lumen", 18, T.TEXT_PRIMARY, 600, ls=-0.2))
        name.addWidget(label("LOCAL ASSISTANT", 8.5, T.TEXT_FAINT, mono=True,
                             ls=1.5))
        brand.addLayout(name)
        brand.addStretch(1)
        v.addLayout(brand)
        v.addWidget(hline(T.BORDER_MED))
        v.addSpacing(14)

        self.nav_rows: dict[str, NavRow] = {}
        nav = vbox(m=(12, 0, 12, 0), s=2)
        for key, text, meta in NAV:
            row = NavRow(key, text, meta, self.switch_to)
            self.nav_rows[key] = row
            nav.addWidget(row)
        v.addLayout(nav)
        v.addStretch(1)

        foot = vbox(m=(24, 0, 24, 0), s=0)
        foot.addWidget(hline(T.BORDER_MED))
        foot.addSpacing(15)
        today = date.today()
        foot.addWidget(label(today.strftime("%a · %b %-d, %Y"), 16,
                             T.TEXT_PRIMARY))
        foot.addSpacing(6)
        model = hbox(s=7)
        model.addWidget(Dot(6, T.OK))
        model.addWidget(label(f"local · {T.MODEL_NAME}", 10, T.TEXT_FAINT,
                              mono=True))
        model.addStretch(1)
        foot.addLayout(model)
        foot.addSpacing(14)

        # No "Quick launch" row here by design — that is Super+L.
        self.settings_row = ClickRow(lambda: self.switch_to("settings"))
        srow = hbox(self.settings_row, (0, 6, 0, 6), 0)
        self.settings_lab = label("Settings", 15, T.TEXT_SECONDARY)
        srow.addWidget(self.settings_lab)
        srow.addStretch(1)
        srow.addWidget(label("⌃,", 10, T.TEXT_FAINT, mono=True))
        foot.addWidget(self.settings_row)
        v.addLayout(foot)
        return bar

    # ---- behavior ---------------------------------------------------------
    def switch_to(self, key: str):
        if key in self.screens:
            self.stack.setCurrentIndex(SCREENS.index(key))

    def _sync_chrome(self, index: int):
        key = SCREENS[index]
        for k, row in self.nav_rows.items():
            row.set_on(k == key)
        pal = self.settings_lab.palette()
        pal.setColor(self.settings_lab.foregroundRole(),
                     qcolor(T.ACCENT if key == "settings" else T.TEXT_SECONDARY))
        self.settings_lab.setPalette(pal)
        # The mock hides the ask bar on Chat (its own composer is right there)
        # and on Settings (a config sheet has nothing to ask about).
        self.askbar.setVisible(key in ASK_SCREENS)
        if key == "chat":
            self.state.warm_model()

    def _ask_context(self) -> dict:
        """What the ask bar tells the model the user is looking at."""
        key = SCREENS[self.stack.currentIndex()]
        screen = self.screens[key]
        if hasattr(screen, "context"):
            return screen.context()
        if key == "mail":
            m = self.state.sel_mail()
            return {"screen": "mail",
                    "message": {"from": m.get("from"), "subject": m.get("subj"),
                                "body": (m.get("body") or "")[:4000]} if m else None}
        if key == "calendar":
            return {"screen": "calendar",
                    "showing": screen.anchor.isoformat(), "view": screen.view}
        if key == "todos":
            return {"screen": "todos",
                    "open": [t["text"] for t in self.state.todos
                             if not t["done"]][:40]}
        if key == "books":
            return {"screen": "books",
                    "recent": [b["title"] for b in self.state.books[:10]]}
        return {"screen": key}

    def _open_chat(self, conv_id: int):
        self.show()
        self.raise_()
        self.activateWindow()
        self.switch_to("chat")
        self.screens["chat"].load_conversation(conv_id)

    def _open_confirm(self, payload: dict):
        # A confirm can arrive while only the hotkey overlay is up; the overlay
        # lives in this window, so surface it before showing the gate.
        self.show()
        self.raise_()
        self.activateWindow()
        self.confirm.open(payload, self._on_confirm_result)

    def _open_compose(self, payload: dict):
        self.show()
        self.raise_()
        self.activateWindow()
        self.compose.open(payload)

    def _open_event(self, payload: dict):
        self.show()
        self.raise_()
        self.activateWindow()
        if not payload.get("date"):
            payload = {**payload, "date": date.today().isoformat()}
        self.event.open(payload)

    def _open_rules(self, prefill: dict):
        self.show()
        self.raise_()
        self.activateWindow()
        self.rules.open(prefill)

    def _on_confirm_result(self, approved: bool, payload: dict):
        confirm_id = payload.get("confirm_id")
        if confirm_id is not None:     # daemon confirm-over-IPC: always answer
            self.state.respond_confirm(confirm_id, approved,
                                       check=payload.get("check_state"))
        if approved and payload.get("toast"):
            self.toast.pop(payload["toast"])

    def _update_badges(self):
        n = self.state.unread_count()
        row = self.nav_rows.get("mail")
        if row is not None:
            row.set_badge(str(n) if n else "3", bool(n))

    def _change_accent(self, hex_color: str):
        """Swap the accent app-wide. Painters capture the accent at construction,
        so the window is rebuilt around the same AppState, geometry, and screen."""
        global _active_window
        if hex_color == T.ACCENT:
            return
        T.set_accent(hex_color)
        _settings().setValue("accent", hex_color)
        self._restyle()

    def _change_font_scale(self, pct: int):
        """Text size affects layout metrics too (control heights, panel widths,
        chip padding), and those are read at construction — so, like the accent,
        it takes a rebuild rather than a repolish."""
        if round(T.FONT_SCALE * 100) == pct:
            return
        T.set_font_scale(pct)
        _settings().setValue("font_scale", pct)
        self._restyle()

    def _restyle(self):
        """Re-apply the app style and rebuild the window around the same
        AppState, geometry, and screen. Painters capture their colors and
        metrics at construction, so nothing short of a rebuild is honest."""
        global _active_window
        QApplication.instance().setPalette(app_palette())
        QApplication.instance().setStyleSheet(build_qss())
        self.state.accent_requested.disconnect(self._change_accent)
        self.state.font_scale_requested.disconnect(self._change_font_scale)
        new = LumenWindow(self.state, self.chat_client)
        new.quit_on_close, self.quit_on_close = self.quit_on_close, False
        new.setGeometry(self.geometry())
        new.switch_to(SCREENS[self.stack.currentIndex()])
        new.show()
        _active_window = new
        self.close()
        self.deleteLater()

    # ---- geometry ---------------------------------------------------------
    def closeEvent(self, ev):
        super().closeEvent(ev)
        if self.quit_on_close:
            QApplication.instance().quit()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        for overlay in (self.confirm, self.compose, self.event, self.rules):
            if overlay.isVisible():
                overlay.setGeometry(self.rect())
        self.toast.reposition()


def _apply_app_style(app: QApplication):
    saved = _settings().value("accent", T.ACCENT_OPTIONS[T.DEFAULT_ACCENT], str)
    T.set_accent(saved)
    T.set_font_scale(_settings().value("font_scale", 100, int))
    # Pin the style: a desktop style plugin (Kvantum et al) repaints widgets and
    # rewrites their palettes on polish, so which one Qt happens to load would
    # otherwise change how the shell looks. setStyle can reset the palette, so
    # ours goes on afterwards.
    app.setStyle(QStyleFactory.create("Fusion"))
    app.setPalette(app_palette())
    f = QFont(T.FONT_SANS)
    f.setPixelSize(14)
    app.setFont(f)
    app.setStyleSheet(build_qss())


def build_window() -> LumenWindow:
    """Factory used by scripts/screenshot.py (sample mode, no daemon)."""
    global _active_window
    _apply_app_style(QApplication.instance())
    _active_window = LumenWindow()
    return _active_window


def main() -> int:
    global _active_window
    app = QApplication(sys.argv)
    _apply_app_style(app)
    _active_window = LumenWindow()
    _active_window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
