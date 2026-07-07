"""Window shell: title bar / tab bar / stacked views, per tokens.md layout.
Holds zero business logic — it routes clicks and keystrokes to screens."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QKeySequence, QShortcut
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QStackedWidget, QVBoxLayout, QWidget

from lumen.ui import theme
from lumen.ui.widgets import button, label

VIEWS = ["launcher", "dashboard", "calendar", "mail", "todos", "books"]


class MainWindow(QWidget):
    sleep_requested = pyqtSignal()

    def __init__(self, screens: dict[str, QWidget]):
        super().__init__()
        self.setWindowTitle("lumen")
        self.setProperty("role", "window")
        self.resize(1320, 800)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # title bar
        titlebar = QWidget()
        titlebar.setProperty("role", "chrome")
        titlebar.setFixedHeight(38)
        tb = QHBoxLayout(titlebar)
        tb.setContentsMargins(14, 0, 14, 0)
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {theme.OK}; font-size: 9px;")
        tb.addWidget(dot)
        tb.addWidget(label("lumen", "secondary"))
        tb.addWidget(label("daily assistant", "dim"))
        tb.addStretch()
        tb.addWidget(label("local · qwen3:4b", "dim"))
        self.sleep_btn = button("idle 10m — sleep", "ghost")
        self.sleep_btn.clicked.connect(self.sleep_requested.emit)
        tb.addWidget(self.sleep_btn)
        root.addWidget(titlebar)

        # tab bar
        tabbar = QWidget()
        tabbar.setProperty("role", "chrome")
        tabbar.setFixedHeight(38)
        tabs = QHBoxLayout(tabbar)
        tabs.setContentsMargins(8, 0, 8, 0)
        tabs.setSpacing(2)
        self._tab_buttons: dict[str, QWidget] = {}
        for i, name in enumerate(VIEWS, start=1):
            b = button(f"{name.capitalize()}  {i}", "tab")
            b.clicked.connect(lambda _, n=name: self.set_view(n))
            self._tab_buttons[name] = b
            tabs.addWidget(b)
        tabs.addStretch()
        self.gear = button("⚙", "tab")
        self.gear.clicked.connect(lambda: self.set_view("settings"))
        tabs.addWidget(self.gear)
        root.addWidget(tabbar)

        # stacked views
        self._stack = QStackedWidget()
        self._names: list[str] = []
        for name in VIEWS + ["settings"]:
            self._stack.addWidget(screens.get(name) or QLabel(f"{name} — coming soon"))
            self._names.append(name)
        root.addWidget(self._stack, 1)

        for i, name in enumerate(VIEWS, start=1):
            QShortcut(QKeySequence(str(i)), self, lambda n=name: self.set_view(n))

        self.set_view("launcher")

    def set_view(self, name: str) -> None:
        self._stack.setCurrentIndex(self._names.index(name))
        for tab_name, b in self._tab_buttons.items():
            b.setProperty("active", "true" if tab_name == name else "false")
            b.style().unpolish(b)
            b.style().polish(b)

    def current_view(self) -> str:
        return self._names[self._stack.currentIndex()]

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Map digit keys 1-6 to set_view for offscreen platform compatibility."""
        if event.text() in "123456":
            digit = int(event.text())
            if digit <= len(VIEWS):
                self.set_view(VIEWS[digit - 1])
                return
        super().keyPressEvent(event)
