"""Settings screen: config.toml-styled sections with toggle switches."""
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QPainter, QPen
from PyQt6.QtWidgets import QAbstractButton, QLabel, QSizePolicy, QWidget

from .. import theme as T
from ..widgets import Switch, font, hbox, hline, label, qcolor, scroll, vbox
from ..state import AppState


class AccentSwatch(QAbstractButton):
    """18px rounded color swatch; the active accent gets a light ring."""

    def __init__(self, hex_color: str, on_pick):
        super().__init__()
        self.hex = hex_color
        self.setFixedSize(18, 18)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(hex_color)
        self.clicked.connect(lambda: on_pick(self.hex))

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        p.setBrush(qcolor(self.hex))
        if self.hex == T.ACCENT:
            p.setPen(QPen(qcolor(T.TEXT_PRIMARY), 1.5))
        else:
            p.setPen(QPen(qcolor(T.BORDER_STRONG), 1))
        p.drawRoundedRect(r, 5, 5)


def _toggle_row(name: str, desc: str, desc_color: str, status: str | None,
                checked: bool, on_toggle) -> QWidget:
    row = QWidget()
    rl = hbox(row, (12, 9, 12, 9), 12)
    n = label(name, 12, T.TEXT_SECONDARY)
    n.setFixedWidth(150)
    rl.addWidget(n)
    rl.addWidget(label(desc, 11, desc_color), 1)
    if status:
        rl.addWidget(label(status, 10, T.OK))
    sw = Switch(checked)
    sw.toggled.connect(on_toggle)
    rl.addWidget(sw)
    return row


def _config_line(key: str, value: str, value_color: str, comment: str = "") -> QLabel:
    html = (f'<span style="color:{T.TEXT_DIM}">{key}</span> = '
            f'<span style="color:{value_color}">{value}</span>')
    if comment:
        html += f' <span style="color:{T.TEXT_FAINT}"># {comment}</span>'
    lab = QLabel(html)
    lab.setFont(font(12))
    lab.setStyleSheet("background: transparent;")
    return lab


class SettingsScreen(QWidget):
    def __init__(self, state: AppState):
        super().__init__()
        self.state = state
        # local stub state; TODO: read/write real config.toml through the daemon
        self.accounts = {"gmail": True, "gcal": True}
        self.mcp = {"search": True, "books": True, "weather": False}

        inner = QWidget()
        outer = hbox(inner, (26, 22, 26, 40), 0)
        col = QWidget()
        col.setMaximumWidth(760 - 52)
        col.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        v = vbox(col, (0, 0, 0, 0), 0)
        outer.addStretch(1)
        outer.addWidget(col, 4)
        outer.addStretch(1)

        head = hbox(s=10)
        head.addWidget(label("Settings", 16, T.TEXT_PRIMARY, 600))
        head.addWidget(label("~/.config/lumen/config.toml", 11, T.TEXT_DIM))
        head.addStretch(1)
        v.addLayout(head)
        v.addSpacing(6)
        v.addWidget(label("# edited here or in your editor — hot-reloaded on save",
                          11, T.TEXT_FAINT))
        v.addSpacing(18)

        # [accounts]
        v.addWidget(label("[accounts]", 12, T.ACCENT))
        v.addSpacing(8)
        v.addWidget(_toggle_row("gmail", "alex@gmail.com · read + send", T.TEXT_DIM,
                                "connected", True, lambda on: self._set("accounts", "gmail", on)))
        v.addWidget(hline(T.BORDER_FAINT))
        v.addWidget(_toggle_row("google_calendar", "primary · read + write", T.TEXT_DIM,
                                "connected", True, lambda on: self._set("accounts", "gcal", on)))
        v.addWidget(hline(T.BORDER_FAINT))
        v.addSpacing(20)

        # [mcp_servers]
        v.addWidget(label("[mcp_servers]", 12, T.ACCENT))
        v.addSpacing(8)
        v.addWidget(_toggle_row("search", "brave-search · stdio", T.TEXT_DIM, None,
                                True, lambda on: self._set("mcp", "search", on)))
        v.addWidget(hline(T.BORDER_FAINT))
        v.addWidget(_toggle_row("books_lookup", "openlibrary · http :7431", T.TEXT_DIM, None,
                                True, lambda on: self._set("mcp", "books", on)))
        v.addWidget(hline(T.BORDER_FAINT))
        v.addWidget(_toggle_row("weather", "not configured", T.TEXT_FAINT, None,
                                False, lambda on: self._set("mcp", "weather", on)))
        v.addWidget(hline(T.BORDER_FAINT))
        v.addSpacing(20)

        # [model] + [sync] as raw config columns
        cols = hbox(s=22)
        model = QWidget()
        model.setMinimumWidth(260)
        mv = vbox(model, (0, 0, 0, 0), 0)
        mv.addWidget(label("[model]", 12, T.ACCENT))
        mv.addSpacing(8)
        for line in (
            _config_line("runtime", '"ollama"', T.OK),
            _config_line("name", '"llama3.1:8b"', T.OK),
            _config_line("context", "8192", T.INFO),
            _config_line("idle_timeout", "300", T.INFO, "unload after 5m"),
        ):
            lw = QWidget()
            ll = vbox(lw, (0, 5, 0, 5), 0)
            ll.addWidget(line)
            mv.addWidget(lw)
        cols.addWidget(model, 1)
        sync = QWidget()
        sync.setMinimumWidth(260)
        sv = vbox(sync, (0, 0, 0, 0), 0)
        sv.addWidget(label("[sync]", 12, T.ACCENT))
        sv.addSpacing(8)
        for line in (
            _config_line("interval", "15", T.INFO, "minutes"),
            _config_line("on_wake", "true", T.WARN),
            _config_line("confirm_writes", "true", T.WARN, "email/calendar"),
            _config_line("catalog_path", '"~/books.db"', T.OK),
        ):
            lw = QWidget()
            ll = vbox(lw, (0, 5, 0, 5), 0)
            ll.addWidget(line)
            sv.addWidget(lw)
        cols.addWidget(sync, 1)
        v.addLayout(cols)
        v.addSpacing(20)

        # [appearance] — accent picker (blue/green/amber/purple/pink)
        v.addWidget(label("[appearance]", 12, T.ACCENT))
        v.addSpacing(8)
        row = QWidget()
        rl = hbox(row, (12, 9, 12, 9), 12)
        n = label("accent", 12, T.TEXT_SECONDARY)
        n.setFixedWidth(150)
        rl.addWidget(n)
        sw = hbox(s=8)
        for name, hex_color in T.ACCENT_OPTIONS.items():
            sw.addWidget(AccentSwatch(hex_color, self.state.accent_requested.emit))
        rl.addLayout(sw)
        current = next((k for k, h in T.ACCENT_OPTIONS.items() if h == T.ACCENT), "custom")
        rl.addWidget(label(f'"{current}" · {T.ACCENT}', 11, T.TEXT_DIM), 1)
        v.addWidget(row)
        v.addWidget(hline(T.BORDER_FAINT))
        v.addStretch(1)

        root = vbox(self)
        root.addWidget(scroll(inner), 1)

    def _set(self, group: str, key: str, on: bool):
        # TODO: persist to config.toml via the daemon
        getattr(self, group)[key] = on
