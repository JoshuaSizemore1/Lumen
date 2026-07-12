"""Main window shell: title bar, tab bar, stacked screens, overlay + toast."""
import sys

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QLabel, QPushButton, QStackedWidget, QWidget,
)

from . import theme as T
from .confirm import ConfirmOverlay, Toast
from .state import AppState
from .styles import build_qss
from .widgets import Chip, Dot, font, hbox, label, vbox
from .screens.launcher import LauncherScreen
from .screens.dashboard import DashboardScreen
from .screens.calendar import CalendarScreen
from .screens.mail import MailScreen
from .screens.todos import TodosScreen
from .screens.books import BooksScreen
from .screens.chat import ChatScreen
from .screens.settings import SettingsScreen

TABS = ("launcher", "dashboard", "calendar", "mail", "todos", "books", "chat", "settings")

_active_window = None  # keeps the rebuilt window alive after an accent switch


def _settings() -> QSettings:
    return QSettings("lumen", "ui_v2")


class TabButton(QPushButton):
    """Web-style underline tab: text label + kbd/badge chip inside the button."""

    def __init__(self, text: str, badge: Chip | None):
        super().__init__()
        self.setProperty("cls", "tab")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(T.TABBAR_H)
        lay = hbox(self, (13, 0, 13, 0), 8)
        self.text_lab = label(text, 12, T.TEXT_DIM)
        self.text_lab.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(self.text_lab)
        if badge is not None:
            badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            lay.addWidget(badge)
        self.toggled.connect(self._recolor)

    def sizeHint(self):
        s = self.layout().sizeHint()
        s.setHeight(T.TABBAR_H)
        return s

    def minimumSizeHint(self):
        return self.sizeHint()

    def _recolor(self, on: bool):
        pal = self.text_lab.palette()
        from .widgets import qcolor
        pal.setColor(self.text_lab.foregroundRole(),
                     qcolor(T.TEXT_PRIMARY if on else T.TEXT_DIM))
        self.text_lab.setPalette(pal)


class LumenWindow(QWidget):
    def __init__(self, state: AppState | None = None):
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle("lumen — daily assistant")
        self.resize(T.WINDOW_W, T.WINDOW_H)
        self.setMinimumSize(1100, 680)

        self.state = state or AppState()
        root = vbox(self)
        root.addWidget(self._build_titlebar())
        root.addWidget(self._build_tabbar())

        self.stack = QStackedWidget()
        self.screens = {
            "launcher": LauncherScreen(self.state),
            "dashboard": DashboardScreen(self.state),
            "calendar": CalendarScreen(self.state),
            "mail": MailScreen(self.state),
            "todos": TodosScreen(self.state),
            "books": BooksScreen(self.state),
            "chat": ChatScreen(self.state),
            "settings": SettingsScreen(self.state),
        }
        for key in TABS:
            self.stack.addWidget(self.screens[key])
        root.addWidget(self.stack, 1)
        self.tab_group.idClicked.connect(self.stack.setCurrentIndex)

        self.overlay = ConfirmOverlay(self)
        self.toast = Toast(self)

        self.state.view_requested.connect(self.switch_to)
        self.state.open_chat_requested.connect(self._open_chat)
        self.state.confirm_requested.connect(self._open_confirm)
        self.state.toast_requested.connect(self.toast.pop)
        self.state.status_requested.connect(self.toast.pop)
        self.state.mails_changed.connect(self._update_mail_badge)
        self.state.accent_requested.connect(self._change_accent)

        for i, key in enumerate(("launcher", "dashboard", "calendar", "todos", "books", "chat")):
            sc = QShortcut(QKeySequence(str(i + 1)), self)
            sc.activated.connect(lambda k=key: self.switch_to(k))

    # ---- chrome ----------------------------------------------------------
    def _build_titlebar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("titlebar")
        bar.setFixedHeight(T.TITLEBAR_H)
        lay = hbox(bar, (14, 0, 14, 0), 0)
        left = hbox(s=9)
        left.addWidget(Dot(8, T.ACCENT))
        left.addWidget(label("lumen", 13, T.TEXT_PRIMARY, 700, ls=0.5))
        left.addWidget(label("daily assistant", 11, T.TEXT_FAINT))
        lay.addLayout(left)
        lay.addStretch(1)
        right = hbox(s=12)
        model = hbox(s=6)
        model.addWidget(Dot(6, T.OK))
        model.addWidget(label(f"local · {T.MODEL_NAME}", 11, T.TEXT_DIM))
        right.addLayout(model)
        right.addWidget(label("|", 11, T.TEXT_FAINT))
        right.addWidget(label("idle 5m → sleep", 11, T.TEXT_DIM))
        right.addWidget(label("|", 11, T.TEXT_FAINT))
        right.addWidget(label("— ▢ ✕", 11, T.TEXT_FAINT, ls=2))
        lay.addLayout(right)
        return bar

    def _build_tabbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("tabbar")
        bar.setFixedHeight(T.TABBAR_H)
        lay = hbox(bar, (8, 0, 8, 0), 2)
        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)

        self.mail_badge = Chip("4", T.ACCENT_ON, T.ACCENT, bg=T.ACCENT,
                               px=10, radius=8, hpad=5, vpad=1, weight=600)
        names = {"launcher": "Launcher", "dashboard": "Dashboard", "calendar": "Calendar",
                 "mail": "Mail", "todos": "Todos", "books": "Books", "chat": "Chat"}
        kbd = {"launcher": "1", "dashboard": "2", "calendar": "3", "todos": "4",
               "books": "5", "chat": "6"}
        self.tab_buttons: dict[str, QPushButton] = {}
        for i, key in enumerate(TABS[:-1]):
            badge = self.mail_badge if key == "mail" else Chip(
                kbd[key], T.TEXT_FAINT, T.BORDER_STRONG, px=10, hpad=4, vpad=0)
            b = TabButton(names[key], badge)
            b.setChecked(key == "launcher")
            self.tab_group.addButton(b, i)
            self.tab_buttons[key] = b
            lay.addWidget(b)
        lay.addStretch(1)

        gear = QPushButton("⚙")
        gear.setProperty("cls", "tabgear")
        gear.setCheckable(True)
        gear.setCursor(Qt.CursorShape.PointingHandCursor)
        gear.setFixedSize(T.TABBAR_H, T.TABBAR_H)
        gear.setToolTip("Settings")
        self.tab_group.addButton(gear, len(TABS) - 1)
        self.tab_buttons["settings"] = gear
        lay.addWidget(gear)

        self._update_mail_badge()
        return bar

    # ---- behavior ---------------------------------------------------------
    def switch_to(self, key: str):
        idx = TABS.index(key)
        self.tab_buttons[key].setChecked(True)
        self.stack.setCurrentIndex(idx)
        if key in ("launcher", "chat"):
            self.state.warm_model()   # user headed somewhere they'll chat → preload

    def _open_chat(self, conv_id: int):
        """Overlay/launcher handoff: surface the window, open the Chat tab, and
        load the same conversation the user was just in."""
        self.show()
        self.raise_()
        self.activateWindow()
        self.switch_to("chat")
        self.screens["chat"].load_conversation(conv_id)

    def _open_confirm(self, payload: dict):
        # A write confirmation can arrive while only the hotkey overlay is up
        # (main window hidden); the in-window overlay must be visible to answer.
        self.show()
        self.raise_()
        self.activateWindow()
        self.overlay.open(payload, self._on_confirm_result)

    def _on_confirm_result(self, approved: bool, payload: dict):
        confirm_id = payload.get("confirm_id")
        if confirm_id is not None:          # daemon confirm-over-IPC: always answer
            self.state.respond_confirm(confirm_id, approved)
        if approved and payload.get("toast"):
            self.toast.pop(payload["toast"])

    def _change_accent(self, hex_color: str):
        """Swap the accent app-wide: retheme QSS and rebuild the window
        (labels/paint code capture the accent at construction). Keeps the
        same AppState, geometry, and current tab."""
        global _active_window
        if hex_color == T.ACCENT:
            return
        T.set_accent(hex_color)
        _settings().setValue("accent", hex_color)
        QApplication.instance().setStyleSheet(build_qss())
        self.state.accent_requested.disconnect(self._change_accent)
        new = LumenWindow(self.state)
        new.setGeometry(self.geometry())
        new.switch_to(TABS[self.stack.currentIndex()])
        new.show()
        _active_window = new
        self.close()
        self.deleteLater()

    def _update_mail_badge(self):
        n = self.state.unread_count()
        self.mail_badge.setText(str(n))
        if n:
            self.mail_badge.restyle(T.ACCENT_ON, T.ACCENT, T.ACCENT)
        else:
            self.mail_badge.restyle(T.TEXT_FAINT, T.BORDER_STRONG, None)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self.overlay.isVisible():
            self.overlay.setGeometry(self.rect())
        self.toast.reposition()


def _apply_app_style(app: QApplication):
    saved = _settings().value("accent", T.ACCENT_OPTIONS[T.DEFAULT_ACCENT], str)
    T.set_accent(saved)
    f = QFont(T.FONT_MONO)
    f.setPixelSize(13)
    app.setFont(f)
    app.setStyleSheet(build_qss())


def build_window() -> LumenWindow:
    """Factory used by scripts/screenshot.py."""
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
