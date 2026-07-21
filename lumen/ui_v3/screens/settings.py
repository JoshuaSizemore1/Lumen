"""Settings — the mockup's config sheet, reading the daemon's live settings.

The mock renders config.toml as syntax-coloured text; that treatment is kept for
read-only values, while everything Lumen can actually change (accents, accounts,
MCP toggles, memory) gets real controls in the same visual language.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QButtonGroup, QFrame, QWidget

from .. import theme as T
from ..components import accent_fill
from ..widgets import (
    ClickLabel, ClickRow, Dot, Switch, button, clear_layout, eyebrow, font,
    hbox, hline, label, scroll, seg_button, vbox,
)


class SettingsScreen(QWidget):
    def __init__(self, state):
        super().__init__()
        self.setObjectName("screen")
        self.state = state
        self._snapshot: dict = {}
        self._learned: dict = {}

        outer = hbox(self, (0, 0, 0, 0), 0)
        host = QWidget()
        hv = hbox(host, (0, 0, 0, 0), 0)
        # max-width:800px, centered. The sheet needs a far larger stretch than
        # the flanking spacers: with equal factors QHBoxLayout would hand each
        # of the three a third of the width instead of letting the sheet grow
        # to its cap first.
        hv.addStretch(1)
        self.sheet = QWidget()
        self.sheet.setMaximumWidth(T.SETTINGS_MAX_W)
        self.lay = vbox(self.sheet, (34, 26, 34, 44), 0)
        hv.addWidget(self.sheet, 100)
        hv.addStretch(1)
        outer.addWidget(scroll(host), 1)

        state.procedures_changed.connect(self.rebuild)
        self.rebuild()

    def showEvent(self, ev):
        super().showEvent(ev)
        self.state.fetch_settings(self._on_settings)
        self.state.fetch_learned(self._on_learned)
        self.state.refresh_procedures()

    def _on_settings(self, snapshot):
        self._snapshot = snapshot if isinstance(snapshot, dict) else {}
        self.rebuild()

    def _on_learned(self, result):
        self._learned = result if isinstance(result, dict) else {}
        self.rebuild()

    # ---- build ------------------------------------------------------------
    def rebuild(self):
        clear_layout(self.lay)
        v = self.lay

        head = hbox(m=(0, 0, 0, 12), s=12)
        head.addWidget(label("Settings", 25, T.TEXT_PRIMARY, 600, ls=-0.4))
        head.addStretch(1)
        head.addWidget(label(self._snapshot.get("config_path",
                                                "~/.config/lumen/config.toml"),
                             10, T.TEXT_FAINT, mono=True))
        v.addLayout(head)
        v.addWidget(hline(T.BORDER_STRONG))
        v.addSpacing(14)
        v.addWidget(label("# edited here or in your editor — hot-reloaded on save",
                          10.5, T.TEXT_FAINTER, mono=True))
        v.addSpacing(20)

        self._accent_section(v)
        self._accounts_section(v)
        self._mcp_section(v)
        self._memory_section(v)
        self._config_section(v)
        v.addStretch(1)

    def _section_label(self, text: str) -> QWidget:
        return label(f"[{text}]", 12, T.ACCENT, mono=True)

    def _row(self, key: str, value: str, right: QWidget | None = None,
             status: str | None = None, status_color: str = T.OK) -> QWidget:
        w = QWidget()
        v = vbox(w, (0, 0, 0, 0), 0)
        row = hbox(m=(12, 10, 12, 10), s=12)
        k = label(key, 12.5, T.TEXT_BODY, mono=True)
        k.setFixedWidth(T.sc(150))
        row.addWidget(k)
        row.addWidget(label(value, 12.5, T.TEXT_MUTED), 1)
        if status:
            row.addWidget(label(status, 10, status_color, mono=True))
        if right is not None:
            row.addWidget(right)
        v.addLayout(row)
        v.addWidget(hline(T.BORDER_FAINT))
        return w

    # ---- sections ---------------------------------------------------------
    def _accent_section(self, v):
        v.addWidget(self._section_label("theme"))
        v.addSpacing(9)
        row = hbox(m=(12, 4, 12, 4), s=12)
        k = label("accent", 12.5, T.TEXT_BODY, mono=True)
        k.setFixedWidth(T.sc(150))
        row.addWidget(k)
        for name, hexv in T.ACCENT_OPTIONS.items():
            sw = ClickRow(lambda h=hexv: self.state.accent_requested.emit(h))
            sw.setFixedSize(T.sc(24), T.sc(24))
            sw.setToolTip(name)
            on = hexv.lower() == T.ACCENT.lower()
            sw.setStyleSheet(
                f"ClickRow {{ background: {hexv}; border-radius: 5px;"
                f" border: 2px solid {T.TEXT_PRIMARY if on else 'transparent'}; }}")
            row.addWidget(sw)
        row.addStretch(1)
        v.addLayout(row)
        v.addSpacing(10)
        self._text_size_row(v)
        v.addSpacing(22)

    def _text_size_row(self, v):
        """Text size as a segmented scale rather than a slider: the steps are
        the only values worth having, and each one is a click instead of a drag
        landing on 113%."""
        row = hbox(m=(12, 4, 12, 4), s=12)
        k = label("text_size", 12.5, T.TEXT_BODY, mono=True)
        k.setFixedWidth(T.sc(150))
        row.addWidget(k)
        current = round(T.FONT_SCALE * 100)
        group = QButtonGroup(self)
        group.setExclusive(True)
        for pct in (50, 75, 100, 125, 150):
            b = seg_button(f"{pct}%")
            b.setChecked(pct == current)
            group.addButton(b)
            b.clicked.connect(lambda _, p=pct: self.state.font_scale_requested.emit(p))
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)

    def _accounts_section(self, v):
        v.addWidget(self._section_label("accounts"))
        v.addSpacing(9)
        accounts = self._snapshot.get("accounts") or {}
        gmail_on = bool((accounts.get("gmail") or {}).get("connected"))
        gcal_on = bool((accounts.get("google_calendar") or {}).get("connected"))
        # Gmail + Calendar share one Google login, so one Connect covers both;
        # only offer it when something is actually disconnected.
        need_google = not (gmail_on and gcal_on)
        v.addWidget(self._account_row("gmail", gmail_on,
                                      connect=need_google and not gmail_on))
        v.addWidget(self._account_row("google_calendar", gcal_on,
                                      connect=need_google and not gcal_on))
        # Canvas login + live status live in the Canvas tab (the web view can't
        # sit inside this config sheet); this row is just a pointer to it.
        v.addWidget(self._row("canvas", "connect & manage in the Canvas tab"))
        v.addSpacing(22)

    def _account_row(self, key: str, connected: bool, connect: bool) -> QWidget:
        """One account line: name, then a dot+word status grouped tight on the
        left (no lonely far-right chip). A Connect button appears only when the
        account is disconnected — a connected account has nothing to press (#6)."""
        w = QWidget()
        v = vbox(w, (0, 0, 0, 0), 0)
        row = hbox(m=(12, 10, 12, 10), s=10)
        k = label(key, 12.5, T.TEXT_BODY, mono=True)
        k.setFixedWidth(T.sc(150))
        row.addWidget(k)
        row.addWidget(Dot(7, T.OK if connected else T.TEXT_FAINTER))
        row.addWidget(label("Connected" if connected else "Not connected", 12.5,
                            T.TEXT_BODY if connected else T.TEXT_FAINTER))
        row.addStretch(1)
        if connect:
            b = button("Connect", "soft", px=11, height=24)
            b.clicked.connect(lambda: self._reconnect_google(b))
            row.addWidget(b)
        v.addLayout(row)
        v.addWidget(hline(T.BORDER_FAINT))
        return w

    def _reconnect_google(self, btn) -> None:
        btn.setEnabled(False)
        btn.setText("Connecting…")
        # A failed/abandoned flow travels the error→status channel, not the
        # reconnect callback, so arm a one-shot rebuild to clear this transient
        # state; success rebuilds via _on_settings, which also disarms it.
        def recover(_msg=None):
            try:
                self.state.status_requested.disconnect(recover)
            except TypeError:
                pass
            self.rebuild()

        def done(snapshot):
            try:
                self.state.status_requested.disconnect(recover)
            except TypeError:
                pass
            self._on_settings(snapshot)

        self.state.status_requested.connect(recover)
        self.state.google_reconnect(done)

    def _mcp_section(self, v):
        v.addWidget(self._section_label("mcp_servers"))
        v.addSpacing(9)
        servers = self._snapshot.get("mcp_servers") or {}
        if not servers:
            v.addWidget(self._row("—", "no servers configured"))
        for name, cfg in servers.items():
            desc = cfg.get("description") or cfg.get("transport") or ""
            v.addWidget(self._row(name, desc or "not configured",
                                  right=Switch(bool(cfg.get("enabled")))))
        v.addSpacing(22)

    def _memory_section(self, v):
        v.addWidget(self._section_label("memory"))
        v.addSpacing(9)

        proposed = self.state.proposed_procedures
        active = self.state.active_procedures
        if proposed:
            v.addWidget(label(
                "Lumen noticed these patterns. Approving one lets it apply "
                "the habit automatically.", 12.5, T.TEXT_MUTED, wrap=True))
            v.addSpacing(8)
        for p in proposed:
            v.addWidget(self._procedure_row(p, proposed=True))
        for p in active:
            v.addWidget(self._procedure_row(p, proposed=False))
        if not proposed and not active:
            v.addWidget(self._row("procedures", "nothing learned yet"))

        v.addSpacing(12)
        text = (self._learned.get("text") or "").strip()
        panel = QFrame()
        panel.setProperty("role", "panel")
        pv = vbox(panel, (14, 12, 14, 12), 8)
        head = hbox(s=8)
        head.addWidget(eyebrow("What Lumen has learned"))
        head.addStretch(1)
        head.addWidget(ClickLabel("open memory.md ↗", 10, T.ACCENT, mono=True,
                                  on_click=self.state.open_memory_file))
        pv.addLayout(head)
        pv.addWidget(label(text or "Nothing distilled yet.", 12.5,
                           T.TEXT_BODY if text else T.TEXT_FAINTER, wrap=True))
        v.addWidget(panel)
        v.addSpacing(22)

    def _procedure_row(self, p: dict, proposed: bool) -> QWidget:
        w = QWidget()
        v = vbox(w, (0, 0, 0, 0), 0)
        row = hbox(m=(12, 9, 12, 9), s=10)
        row.addWidget(Dot(6, T.WARN if proposed else T.OK))
        row.addWidget(label(p.get("text") or p.get("summary", ""), 12.5,
                            T.TEXT_BODY, wrap=True), 1)
        slug = p.get("slug", "")
        if proposed:
            ok = button("Approve", "soft", px=11, height=24)
            ok.clicked.connect(lambda: self.state.approve_procedure(slug))
            row.addWidget(ok)
            no = ClickLabel("✕", 12, T.TEXT_GHOST, tooltip="Dismiss",
                            on_click=lambda: self.state.dismiss_procedure(slug))
            row.addWidget(no)
        else:
            rm = ClickLabel("forget", 10, T.TEXT_FAINT, mono=True,
                            on_click=lambda: self.state.remove_procedure(slug))
            row.addWidget(rm)
        v.addLayout(row)
        v.addWidget(hline(T.BORDER_FAINT))
        return w

    def _config_section(self, v):
        """Read-only values, in the mock's syntax-coloured TOML treatment."""
        cols = hbox(s=26)
        model = self._snapshot.get("model") or {}
        sync = self._snapshot.get("sync") or {}
        cols.addWidget(self._toml_block("model", [
            ("runtime", model.get("runtime", "ollama"), "str"),
            ("name", model.get("name", T.MODEL_NAME), "str"),
            ("context", model.get("context", 8192), "num"),
            ("idle_timeout", model.get("idle_timeout", 600), "num",
             "# unload when idle"),
        ]), 1)
        cols.addWidget(self._toml_block("sync", [
            ("interval", sync.get("interval", 5), "num", "# minutes"),
            ("on_wake", sync.get("on_wake", True), "bool"),
            ("confirm_writes", sync.get("confirm_writes", True), "bool"),
            ("load_remote_images", self.state.load_remote_images, "bool"),
        ]), 1)
        v.addLayout(cols)

    def _toml_block(self, name: str, rows: list[tuple]) -> QWidget:
        w = QWidget()
        v = vbox(w, (0, 0, 0, 0), 0)
        v.addWidget(self._section_label(name))
        v.addSpacing(9)
        for row in rows:
            key, value, kind = row[0], row[1], row[2]
            comment = row[3] if len(row) > 3 else ""
            line = hbox(m=(0, 3, 0, 3), s=6)
            line.addWidget(label(key, 12.5, T.TEXT_FAINT, mono=True))
            line.addWidget(label("=", 12.5, T.TEXT_BODY, mono=True))
            if kind == "str":
                shown, color = f'"{value}"', T.OK
            elif kind == "num":
                shown, color = str(value), T.INFO
            else:
                shown, color = str(bool(value)).lower(), T.WARN
            line.addWidget(label(shown, 12.5, color, mono=True))
            if comment:
                line.addWidget(label(comment, 12.5, T.TEXT_FAINTER, mono=True))
            line.addStretch(1)
            v.addLayout(line)
        v.addStretch(1)
        return w
