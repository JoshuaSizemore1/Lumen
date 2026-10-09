"""Settings — the mockup's config sheet, reading the daemon's live settings.

The mock renders config.toml as syntax-coloured text; that treatment is kept for
read-only values, while everything Lumen can actually change (accents, accounts,
MCP toggles, memory) gets real controls in the same visual language.
"""
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QButtonGroup, QFrame, QWidget

from .. import theme as T
from ..components import accent_fill
from ..widgets import (
    ClickLabel, ClickRow, Dot, ProgressBar, Select, SegmentedControl, Switch,
    button, clear_layout, eyebrow, font, hbox, hline, label, scroll, seg_button,
    vbox,
)


class SettingsScreen(QWidget):
    def __init__(self, state):
        super().__init__()
        self.setObjectName("screen")
        self.state = state
        self._snapshot: dict = {}
        self._learned: dict = {}
        self._rules: list[dict] = []
        self.model_switch = None      # rebuilt each pass; see _model_section
        self._flash_model = False     # arrive-from-a-notice highlight (one shot)

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
        state.model_state_changed.connect(self.rebuild)
        # Clicking a "the model is off" notice anywhere in the app lands here;
        # flashing the row is what makes it land on the *switch* rather than
        # merely on this page.
        state.model_switch_highlight_requested.connect(self._highlight_model)
        self.rebuild()

    def showEvent(self, ev):
        super().showEvent(ev)
        self.state.fetch_settings(self._on_settings)
        self.state.fetch_learned(self._on_learned)
        self.state.refresh_procedures()
        self.state.list_rules(self._on_rules)

    def _on_rules(self, result):
        self._rules = (result or {}).get("rules", []) if isinstance(result, dict) else []
        self.rebuild()

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

        self._model_section(v)
        self._accent_section(v)
        self._accounts_section(v)
        self._rules_section(v)
        self._mcp_section(v)
        self._memory_section(v)
        self._config_section(v)
        v.addSpacing(26)
        self._quit_section(v)
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
    # ---- model mode: off / local / claude --------------------------------
    def _model_section(self, v):
        """3-way mode control: Off / Local / Claude.

        First section on the page — the one setting that changes what the whole
        app can do, and where every model-off / claude-unavailable notice points.
        """
        v.addWidget(self._section_label("model"))
        v.addSpacing(9)

        model = self._snapshot.get("model") or {}
        # Prefer the snapshot's mode; fall back to cached state before the first
        # settings.get reply arrives.
        mode = model.get("mode") or getattr(self.state, "model_mode", "local")
        if mode not in ("off", "local", "claude"):
            mode = "off" if not model.get("enabled", True) else "local"

        # The mode row (gets flash-highlighted when arriving from a notice).
        self.model_row = QWidget()
        self.model_row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        rv = vbox(self.model_row, (0, 0, 0, 0), 0)
        row = hbox(m=(12, 10, 12, 10), s=10)
        k = label("mode", 12.5, T.TEXT_BODY, mono=True)
        k.setFixedWidth(T.sc(150))
        row.addWidget(k)
        self.model_seg = SegmentedControl(
            [("off", "Off"), ("local", "Local"), ("claude", "Claude")],
            selected=mode,
            on_change=self._set_mode,
        )
        self.model_switch = None   # no Switch widget; tests that filter by identity still work
        row.addWidget(self.model_seg)
        row.addStretch(1)
        rv.addLayout(row)
        rv.addWidget(hline(T.BORDER_FAINT))
        v.addWidget(self.model_row)

        # Mode-dependent sub-section.
        if mode == "off":
            v.addWidget(label(
                "Off means the model never loads — no RAM, no fan. Mail, "
                "calendar, todos, Canvas and search keep working; anything "
                "that needs the model says so and points back here.",
                11.5, T.TEXT_MUTED, wrap=True))
            v.addSpacing(6)
        elif mode == "local":
            local_name = (model.get("local_name") or model.get("name")
                          or T.MODEL_NAME)
            v.addWidget(self._row("name", local_name))
            v.addWidget(label(
                "Off means the model never loads — no RAM, no fan. Mail, "
                "calendar, todos, Canvas and search keep working; anything "
                "that needs the model says so and points back here.",
                11.5, T.TEXT_MUTED, wrap=True))
            v.addSpacing(6)
        else:
            self._claude_rows(v)

        v.addSpacing(22)
        if self._flash_model:
            self._flash_model = False
            self._paint_flash()

    def _set_mode(self, mode: str) -> None:
        # The daemon's reply drives model_state_changed → rebuild.
        self.state.set_model_mode(mode)
        self.state.toast_requested.emit(
            {"off": "Model off — unloaded from memory",
             "local": "Local model on",
             "claude": "Claude mode on"}.get(mode, f"Mode: {mode}"))

    def _claude_rows(self, v) -> None:
        """Rows shown only in Claude mode: model picker, CLI status, usage."""
        model = self._snapshot.get("model") or {}
        claude = model.get("claude") or {}
        claude_models = (model.get("claude_models")
                         or {"haiku": "Haiku 4.5", "sonnet": "Sonnet 5"})
        current_cm = (model.get("claude_model")
                      or getattr(self.state, "model_claude_model", "haiku"))

        # claude_model dropdown
        cw = QWidget()
        rv = vbox(cw, (0, 0, 0, 0), 0)
        dr = hbox(m=(12, 10, 12, 10), s=12)
        ck = label("claude_model", 12.5, T.TEXT_BODY, mono=True)
        ck.setFixedWidth(T.sc(150))
        dr.addWidget(ck)
        sel = Select()
        for key, display in claude_models.items():
            sel.addItem(display, key)
        for i in range(sel.count()):
            if sel.itemData(i) == current_cm:
                sel.setCurrentIndex(i)
                break
        sel.currentIndexChanged.connect(
            lambda _idx, s=sel: self._set_claude_model(s.currentData()))
        dr.addWidget(sel)
        dr.addStretch(1)
        rv.addLayout(dr)
        rv.addWidget(hline(T.BORDER_FAINT))
        v.addWidget(cw)

        # CLI status row
        installed = claude.get("installed", True)
        logged_in = claude.get("logged_in")
        account = claude.get("account") or ""
        if not installed:
            cli_val, cli_status, cli_col = "", "not installed", T.WARN
        elif logged_in is False:
            cli_val = ""
            cli_status = "logged out — run `claude auth login`"
            cli_col = T.WARN
        else:
            cli_val = "installed"
            cli_status = f"logged in as {account}" if account else ""
            cli_col = T.OK
        v.addWidget(self._row("cli", cli_val,
                               status=cli_status if cli_status else None,
                               status_color=cli_col))

        # Usage bars (hidden when daemon hasn't reported usage yet)
        usage = claude.get("usage")
        if usage:
            fh = usage.get("five_hour") or {}
            sd = usage.get("seven_day") or {}
            v.addWidget(self._usage_row(fh, sd))

        # Privacy note
        v.addWidget(label(
            "Answers and the email/calendar/Canvas text they use are sent to "
            "Anthropic. The local model stays unloaded.",
            11.5, T.TEXT_MUTED, wrap=True))
        v.addSpacing(6)

    def _set_claude_model(self, key: str) -> None:
        if not key:
            return
        self.state.set_claude_model(key)

    def _usage_row(self, five_hour: dict, seven_day: dict) -> QWidget:
        """Two inline mini progress bars: '5h ░░ 22%  7d ░ 9%'."""
        w = QWidget()
        rv = vbox(w, (0, 0, 0, 0), 0)
        row = hbox(m=(12, 10, 12, 10), s=20)
        k = label("usage", 12.5, T.TEXT_BODY, mono=True)
        k.setFixedWidth(T.sc(150))
        row.addWidget(k)
        for window_lbl, info in (("5h", five_hour), ("7d", seven_day)):
            pct = float(info.get("utilization", 0.0))
            pct_int = round(pct * 100)
            grp = hbox(s=5)
            grp.addWidget(label(window_lbl, 10, T.TEXT_FAINT, mono=True))
            bar = ProgressBar(pct)
            bar.setFixedWidth(T.sc(56))
            grp.addWidget(bar)
            grp.addWidget(label(f"{pct_int}%", 10, T.TEXT_MUTED, mono=True))
            row.addLayout(grp)
        row.addStretch(1)
        rv.addLayout(row)
        rv.addWidget(hline(T.BORDER_FAINT))
        return w

    def _set_model(self, on: bool) -> None:
        # Legacy: kept so any existing callers (e.g. test stubs) don't crash.
        self.state.set_model_enabled(on)

    def _highlight_model(self):
        """Arriving from a model-off notice. The rebuild that follows the tab
        switch would drop any styling applied now, so arm a flag it honours."""
        self._flash_model = True
        self._paint_flash()

    def _paint_flash(self):
        row = getattr(self, "model_row", None)
        if row is None:
            return
        row.setStyleSheet(f"QWidget {{ background: {accent_fill()};"
                          f" border-left: 2px solid {T.ACCENT}; }}")
        QTimer.singleShot(1600, lambda: row.setStyleSheet("")
                          if row is not None else None)

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
        v.addWidget(self._account_row(
            "gmail", gmail_on, connect=need_google and not gmail_on,
            enabled=bool((accounts.get("gmail") or {}).get("enabled", True))))
        v.addWidget(self._account_row(
            "google_calendar", gcal_on, connect=need_google and not gcal_on,
            enabled=bool((accounts.get("google_calendar") or {}).get("enabled", True))))
        # Canvas: login/connect still happens in the Canvas tab (the web view
        # can't sit inside this config sheet), but the connected/not-connected
        # status now shows here just like Gmail/Calendar (#37). When it is down
        # the row points to the Canvas tab instead of a dead Connect button.
        canvas_on = bool((accounts.get("canvas") or {}).get("connected"))
        v.addWidget(self._account_row(
            "canvas", canvas_on, connect=False,
            hint_tab=None if canvas_on else "canvas",
            enabled=bool((accounts.get("canvas") or {}).get("enabled", True))))
        v.addSpacing(22)

    def _account_row(self, key: str, connected: bool, connect: bool,
                     hint_tab: str | None = None, enabled: bool = True) -> QWidget:
        """One account line: name, then a dot+word status grouped tight on the
        left. A disconnected account offers Connect (#6) or a tab pointer (#37);
        a connected one offers Disable/Enable (pause sync, keep login) and
        Disconnect (remove login) — #38."""
        w = QWidget()
        v = vbox(w, (0, 0, 0, 0), 0)
        row = hbox(m=(12, 10, 12, 10), s=10)
        k = label(key, 12.5, T.TEXT_BODY, mono=True)
        k.setFixedWidth(T.sc(150))
        row.addWidget(k)
        if connected and not enabled:
            status, color = "Paused", T.WARN
        elif connected:
            status, color = "Connected", T.TEXT_BODY
        else:
            status, color = "Not connected", T.TEXT_FAINTER
        dot = T.WARN if (connected and not enabled) else (
            T.OK if connected else T.TEXT_FAINTER)
        row.addWidget(Dot(7, dot))
        row.addWidget(label(status, 12.5, color))
        row.addStretch(1)
        if connect:
            b = button("Connect", "soft", px=11, height=24)
            b.clicked.connect(lambda: self._reconnect_google(b))
            row.addWidget(b)
        elif not connected and hint_tab is not None:
            row.addWidget(ClickLabel(
                "connect in the Canvas tab ↗", 11, T.ACCENT, mono=True,
                on_click=lambda: self.state.view_requested.emit(hint_tab)))
        if connected:
            # The routes reply with just {accounts}; re-fetch the whole snapshot
            # so every section stays intact when the row rebuilds.
            refresh = lambda _r=None: self.state.fetch_settings(self._on_settings)
            toggle = button("Enable" if not enabled else "Disable", "soft",
                            px=11, height=24)
            toggle.clicked.connect(
                lambda: self.state.set_connection_enabled(key, not enabled, refresh))
            row.addWidget(toggle)
            disc = button("Disconnect", "ghost", px=11, height=24)
            disc.clicked.connect(
                lambda: self.state.disconnect_connection(key, refresh))
            row.addWidget(disc)
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

    def _rules_section(self, v):
        # #41: mail rules were only creatable/editable from the mail screen and
        # the chat path; there was no place to see them all and turn one off or
        # remove it. This lists every rule with an enable switch and a delete,
        # and a click opens the existing editor for changes.
        v.addWidget(self._section_label("mail_rules"))
        v.addSpacing(9)
        if not self._rules:
            v.addWidget(self._row("—", "no mail rules yet"))
        for r in self._rules:
            v.addWidget(self._rule_row(r))
        add = button("+ New rule", "soft", px=11, height=26)
        add.clicked.connect(lambda: self.state.open_rule_editor())
        wrap = hbox(m=(12, 8, 12, 4), s=0)
        wrap.addWidget(add)
        wrap.addStretch(1)
        v.addLayout(wrap)
        v.addSpacing(22)

    def _rule_row(self, r: dict) -> QWidget:
        rid = r.get("id")
        conds = []
        for key, pfx in (("from_addrs", "from "), ("domains", "@"),
                         ("subject_kw", "subject~"), ("body_kw", "body~")):
            if r.get(key):
                conds.append(pfx + ", ".join(r[key]))
        summary = "; ".join(conds) or "no conditions"

        w = QWidget()
        outer = vbox(w, (0, 0, 0, 0), 0)
        row = hbox(m=(12, 8, 12, 8), s=10)
        # Clicking the label/summary opens the editor (edit/rename/retune).
        name = ClickLabel(r.get("label", "?"), 12.5, T.TEXT_BODY, weight=600,
                          on_click=lambda: self.state.open_rule_editor(r))
        name.setFixedWidth(T.sc(150))
        row.addWidget(name)
        row.addWidget(label(summary, 11.5, T.TEXT_MUTED), 1)
        sw = Switch(bool(r.get("enabled", True)))
        sw.toggled.connect(lambda on: self.state.toggle_rule(
            rid, on, self._on_rules))
        row.addWidget(sw)
        row.addWidget(ClickLabel("✕", 12, T.TEXT_GHOST,
                                 on_click=lambda: self.state.delete_rule(
                                     rid, self._on_rules),
                                 tooltip="Delete rule"))
        outer.addLayout(row)
        outer.addWidget(hline(T.BORDER_FAINT))
        return w

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

    # ---- stop everything (#47) -------------------------------------------
    def _quit_section(self, v):
        """A way to stop the background daemon from inside the app.

        The daemon outlives the window on purpose (it is what keeps mail and
        Canvas in sync), which is also how a stray one ends up running with no
        window to reach it from. `lumen --quit` does this from a terminal;
        this is the same sweep, one click, for when there isn't one."""
        from lumen import instance_lock

        v.addWidget(self._section_label("background"))
        v.addSpacing(9)
        try:
            running = len(instance_lock.daemon_pids())
        except OSError:
            running = -1
        state = ("couldn't check" if running < 0 else
                 "not running" if running == 0 else
                 f"{running} running" if running > 1 else "running")
        stop = button("Stop background sync", "soft", px=12, height=28)
        stop.setToolTip("Ends the daemon. Lumen restarts it next time you "
                        "open the app; nothing saved is lost.")
        stop.clicked.connect(self._stop_daemons)
        v.addWidget(self._row("daemon", f"syncs mail, calendar and Canvas — {state}",
                              right=stop))
        quit_all = button("Quit Lumen", "ghost", px=12, height=28)
        quit_all.setToolTip("Closes the window and stops the daemon with it.")
        quit_all.clicked.connect(self._quit_everything)
        v.addWidget(self._row("quit", "close the window and stop syncing",
                              right=quit_all))

    def _stop_daemons(self):
        from lumen import instance_lock
        pids = instance_lock.daemon_pids()
        if not pids:
            self.state.toast_requested.emit("No background sync was running")
        else:
            instance_lock.terminate(pids)
            self.state.toast_requested.emit(
                f"Stopped background sync ({len(pids)})")
        self.rebuild()

    def _quit_everything(self):
        from PyQt6.QtWidgets import QApplication

        from lumen import instance_lock
        instance_lock.terminate(instance_lock.daemon_pids())
        QApplication.instance().quit()

    def _config_section(self, v):
        """Read-only values, in the mock's syntax-coloured TOML treatment."""
        cols = hbox(s=26)
        model = self._snapshot.get("model") or {}
        sync = self._snapshot.get("sync") or {}
        cols.addWidget(self._toml_block("model", [
            ("runtime", model.get("runtime", "ollama"), "str"),
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
