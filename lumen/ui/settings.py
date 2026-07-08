"""Settings: a config file rendered nicely. Flat, no sidebar. Static this phase;
live toggles/hot-reload arrive with the features they control."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from lumen.ui import theme
from lumen.ui.widgets import label

ACCOUNTS = [("gmail", "not connected · Phase 6", False),
            ("google_calendar", "not connected · Phase 5", False)]
SERVERS = [("search", "brave-search · Phase 3", False),
           ("books_lookup", "openlibrary · Phase 4", False)]
MODEL_KV = [("runtime", '"ollama"'), ("name", '"qwen3:4b"'),
            ("idle_unload_minutes", "10  # unload after 10m")]
SYNC_KV = [("interval", "5  # minutes"), ("confirm_writes", "true  # email/calendar")]


def _kv_block(header: str, rows: list[tuple[str, str]]) -> QWidget:
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.addWidget(label(header, "accent-eyebrow"))
    for k, val in rows:
        v.addWidget(label(f"{k} = {val}", "muted"))
    return w


class SettingsScreen(QWidget):
    def __init__(self):
        super().__init__()
        outer = QHBoxLayout(self)
        col = QWidget()
        col.setMaximumWidth(760)
        outer.addWidget(col, alignment=Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        root = QVBoxLayout(col)
        root.setContentsMargins(26, 22, 26, 22)

        head = QHBoxLayout()
        head.addWidget(label("Settings", "h2"))
        head.addWidget(label("lumen/config.toml", "sub"))
        head.addStretch()
        root.addLayout(head)
        root.addWidget(label("# edited here or in your editor", "faint"))

        for section, rows in (("[accounts]", ACCOUNTS), ("[mcp_servers]", SERVERS)):
            root.addWidget(label(section, "accent-eyebrow"))
            for key, desc, on in rows:
                row = QHBoxLayout()
                row.addWidget(label(key, "secondary"))
                row.addWidget(label(desc, "dim"), 1)
                sw = QCheckBox()
                sw.setChecked(on)
                sw.setEnabled(False)  # cosmetic until the feature phase wires it
                row.addWidget(sw)
                root.addLayout(row)

        cols = QGridLayout()
        cols.addWidget(_kv_block("[model]", MODEL_KV), 0, 0)
        cols.addWidget(_kv_block("[sync]", SYNC_KV), 0, 1)
        root.addLayout(cols)
        root.addStretch()
