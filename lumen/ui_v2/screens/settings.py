"""Settings screen: config.toml-styled sections rendered from the live daemon
config (settings.get). Reflect-only — you change values by editing config.toml
(it hot-reloads). The accent picker is the one interactive control."""
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QPainter, QPen
from PyQt6.QtWidgets import QAbstractButton, QLabel, QSizePolicy, QWidget

from .. import theme as T
from ..widgets import (Chip, ClickLabel, Dot, Switch, button, clear_layout,
                       empty_state, font, hbox, hline, label, qcolor, scroll,
                       vbox)
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

        # [accounts] — reflect-only status (edit config.toml / run auth to change)
        v.addWidget(label("[accounts]", 12, T.ACCENT))
        v.addSpacing(8)
        self._accounts_box = QWidget()
        vbox(self._accounts_box, (0, 0, 0, 0), 0)
        v.addWidget(self._accounts_box)
        v.addSpacing(20)

        # [mcp_servers]
        v.addWidget(label("[mcp_servers]", 12, T.ACCENT))
        v.addSpacing(8)
        self._mcp_box = QWidget()
        vbox(self._mcp_box, (0, 0, 0, 0), 0)
        v.addWidget(self._mcp_box)
        v.addSpacing(20)

        # [model] + [sync] as raw config columns
        cols = hbox(s=22)
        model = QWidget()
        model.setMinimumWidth(260)
        mv = vbox(model, (0, 0, 0, 0), 0)
        mv.addWidget(label("[model]", 12, T.ACCENT))
        mv.addSpacing(8)
        self._model_box = QWidget()
        vbox(self._model_box, (0, 0, 0, 0), 10)
        mv.addWidget(self._model_box)
        cols.addWidget(model, 1)
        sync = QWidget()
        sync.setMinimumWidth(260)
        sv = vbox(sync, (0, 0, 0, 0), 0)
        sv.addWidget(label("[sync]", 12, T.ACCENT))
        sv.addSpacing(8)
        self._sync_box = QWidget()
        vbox(self._sync_box, (0, 0, 0, 0), 10)
        sv.addWidget(self._sync_box)
        cols.addWidget(sync, 1)
        v.addLayout(cols)
        v.addSpacing(20)

        # seed the dynamic sections until the first snapshot arrives
        for box in (self._accounts_box, self._mcp_box, self._model_box,
                    self._sync_box):
            box.layout().addWidget(label("loading…", 11, T.TEXT_FAINT))

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
        v.addSpacing(20)

        # [mail_rules] — deterministic inbox rules (2026-07-15)
        v.addWidget(label("[mail_rules]", 12, T.ACCENT))
        v.addSpacing(8)
        v.addWidget(label("New mail matching a rule is labeled and leaves the "
                          "inbox — automatic, no model involved.", 11, T.TEXT_DIM))
        v.addSpacing(8)
        new_rule = button("＋ New rule", "ghost", 12, 30)
        new_rule.clicked.connect(lambda: self.state.open_rule_editor())
        nr = hbox(s=8)
        nr.addWidget(new_rule)
        nr.addStretch(1)
        v.addLayout(nr)
        v.addSpacing(12)
        self._rules_box = QWidget()
        vbox(self._rules_box, (0, 0, 0, 0), 6)
        v.addWidget(self._rules_box)
        v.addSpacing(20)

        # [memory] — what Lumen has learned + supervised procedures (Phase 9)
        v.addWidget(label("[memory]", 12, T.ACCENT))
        v.addSpacing(8)
        v.addWidget(label("Lumen learns your patterns into a small, editable "
                          "file. Deleting a line corrects it.", 11, T.TEXT_DIM))
        v.addSpacing(8)
        view_btn = button("View what Lumen has learned", "ghost", 12, 30)
        view_btn.clicked.connect(self.state.open_memory_file)
        vr = hbox(s=8)
        vr.addWidget(view_btn)
        vr.addStretch(1)
        v.addLayout(vr)
        v.addSpacing(12)
        self._proc_box = QWidget()
        vbox(self._proc_box, (0, 0, 0, 0), 6)
        v.addWidget(self._proc_box)
        v.addStretch(1)

        self.state.procedures_changed.connect(self._rebuild_procedures)
        self._rebuild_procedures()

        root = vbox(self)
        root.addWidget(scroll(inner), 1)

    def showEvent(self, ev):
        super().showEvent(ev)
        self.state.refresh_procedures()
        self.state.fetch_settings(self._on_settings)
        self.state.list_rules(self._on_rules)

    # ---- live snapshot -----------------------------------------------------

    def _status_row(self, name: str, detail: str, state_text: str,
                    ok: bool, hint: str | None = None) -> QWidget:
        row = QWidget()
        rl = hbox(row, (12, 9, 12, 9), 12)
        n = label(name, 12, T.TEXT_SECONDARY)
        n.setFixedWidth(150)
        rl.addWidget(n)
        rl.addWidget(label(detail, 11, T.TEXT_DIM), 1)
        if hint:
            rl.addWidget(label(hint, 10, T.TEXT_FAINT))
        rl.addWidget(Dot(7, T.OK if ok else T.TEXT_GHOST))
        rl.addWidget(label(state_text, 10, T.OK if ok else T.TEXT_MUTED))
        return row

    def _on_settings(self, result: dict):
        if not isinstance(result, dict) or "model" not in result:
            for box, msg in ((self._accounts_box, "daemon offline"),
                             (self._mcp_box, ""), (self._model_box, ""),
                             (self._sync_box, "")):
                clear_layout(box.layout())
                if msg:
                    box.layout().addWidget(empty_state(msg))
            return
        self._populate(result)

    def _populate(self, snap: dict):
        acc = snap["accounts"]
        box = self._accounts_box.layout()
        clear_layout(box)
        gm, gc = acc["gmail"]["connected"], acc["google_calendar"]["connected"]
        box.addWidget(self._status_row(
            "gmail", "read + modify", "connected" if gm else "not connected", gm,
            None if gm else "run: lumen-google-auth"))
        box.addWidget(hline(T.BORDER_FAINT))
        box.addWidget(self._status_row(
            "google_calendar", "read + write",
            "connected" if gc else "not connected", gc,
            None if gc else "run: lumen-google-auth"))
        box.addWidget(hline(T.BORDER_FAINT))

        box = self._mcp_box.layout()
        clear_layout(box)
        servers = snap["mcp"]["servers"]
        enabled = snap["mcp"]["enabled"]
        if servers:
            for s in servers:
                box.addWidget(self._status_row(
                    s["name"], s["detail"],
                    "enabled" if s["enabled"] else "off", s["enabled"]))
                box.addWidget(hline(T.BORDER_FAINT))
            if not enabled:
                box.addWidget(label("# mcp disabled — enable in config.toml",
                                    10, T.TEXT_FAINT))
        else:
            box.addWidget(label("no MCP servers configured", 11, T.TEXT_FAINT))

        m, sy = snap["model"], snap["sync"]
        box = self._model_box.layout()
        clear_layout(box)
        esc = m["escalation_model"]
        for line in (
            _config_line("runtime", f'"{m["runtime"]}"', T.OK),
            _config_line("name", f'"{m["name"]}"', T.OK),
            _config_line("escalation", f'"{esc}"' if esc else "unset",
                         T.OK if esc else T.TEXT_MUTED),
            _config_line("context", str(m["num_ctx"]), T.INFO),
            _config_line("idle_timeout", str(m["idle_unload_minutes"]), T.INFO,
                         "min · idle-unload"),
        ):
            box.addWidget(line)

        box = self._sync_box.layout()
        clear_layout(box)
        for line in (
            _config_line("gmail_poll", str(sy["gmail_poll_minutes"]), T.INFO, "min"),
            _config_line("calendar_poll", str(sy["calendar_poll_minutes"]), T.INFO, "min"),
            _config_line("gmail_window", str(sy["gmail_window_months"]), T.INFO, "months"),
            _config_line("db", f'"{snap["paths"]["db"]}"', T.OK),
        ):
            box.addWidget(line)

    # ---- mail rules (2026-07-15) --------------------------------------------

    def _on_rules(self, result: dict):
        box = self._rules_box.layout()
        clear_layout(box)
        rules = (result or {}).get("rules", [])
        if not rules:
            box.addWidget(label("No rules yet — say “create a rule…” in chat, "
                                "or use ＋ New rule.", 11, T.TEXT_FAINT))
            return
        for r in rules:
            box.addWidget(self._rule_row(r))

    @staticmethod
    def _rule_summary(r: dict) -> str:
        parts = []
        for key, name in (("from_addrs", "from"), ("domains", "domain"),
                          ("subject_kw", "subject"), ("body_kw", "body")):
            if r[key]:
                parts.append(f"{name}: {', '.join(r[key][:3])}")
        return " · ".join(parts)

    def _rule_row(self, r: dict) -> QWidget:
        row = QWidget()
        rl = hbox(row, (12, 8, 12, 8), 10)
        c = T.label_color(r["label"])
        rl.addWidget(Chip(r["label"], c, c, px=10, radius=7, hpad=7, vpad=2))
        rl.addWidget(label(self._rule_summary(r), 11, T.TEXT_DIM), 1)
        sw = Switch(r["enabled"])
        sw.clicked.connect(lambda _=False, rid=r["id"], s=sw:
                           self.state.toggle_rule(rid, s.isChecked()))
        rl.addWidget(sw)
        edit = button("Edit", "ghost", 11, 26)
        edit.clicked.connect(lambda _=False, rr=r: self.state.open_rule_editor(rr))
        rl.addWidget(edit)
        rl.addWidget(ClickLabel(
            "✕", 12, T.TEXT_FAINT,
            on_click=lambda rid=r["id"]: self.state.delete_rule(
                rid, lambda _r: self.state.list_rules(self._on_rules)),
            tooltip="Delete rule"))
        return row

    # ---- supervised procedures (Phase 9) -----------------------------------

    def _proc_row(self, proc: dict, actions: list[tuple]) -> QWidget:
        row = QWidget()
        rl = hbox(row, (12, 8, 12, 8), 10)
        rl.addWidget(label(proc.get("name") or proc.get("slug", "routine"),
                           12, T.TEXT_SECONDARY), 1)
        for text, variant, handler in actions:
            b = button(text, variant, 11, 26)
            b.clicked.connect(handler)
            rl.addWidget(b)
        return row

    def _rebuild_procedures(self):
        box = self._proc_box.layout()
        clear_layout(box)
        proposed = self.state.proposed_procedures
        active = self.state.active_procedures
        if proposed:
            box.addWidget(label("PROPOSED ROUTINES", 10, T.TEXT_DIM, 600))
            for p in proposed:
                slug = p["slug"]
                box.addWidget(self._proc_row(p, [
                    ("Approve", "primary",
                     lambda _=False, s=slug: self.state.approve_procedure(s)),
                    ("Dismiss", "ghost",
                     lambda _=False, s=slug: self.state.dismiss_procedure(s))]))
        if active:
            box.addWidget(label("ACTIVE ROUTINES", 10, T.TEXT_DIM, 600))
            for p in active:
                slug = p["slug"]
                box.addWidget(self._proc_row(p, [
                    ("Remove", "ghost",
                     lambda _=False, s=slug: self.state.remove_procedure(s))]))
        if not proposed and not active:
            box.addWidget(label("No learned routines yet.", 11, T.TEXT_FAINT))
