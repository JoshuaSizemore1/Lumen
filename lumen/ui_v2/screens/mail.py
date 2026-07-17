"""Mail screen: 334px message list | reading pane."""
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QFrame, QLabel, QLineEdit, QWidget

from .. import theme as T
from ..state import AppState
from ..widgets import (
    Chip, ClickChip, ClickRow, Dot, ElideLabel, FlowLayout, HtmlBody, button,
    clear_layout, empty_state, font, hbox, label, qcolor, scroll, vbox, vline,
)


class MailScreen(QWidget):
    def __init__(self, state: AppState):
        super().__init__()
        self.state = state

        root = hbox(self)

        # ---- list column (fixed 334, sticky header outside the scroll) ----
        col = QWidget()
        col.setFixedWidth(334)
        cv = vbox(col)
        head = QWidget()
        hv = vbox(head, (16, 16, 16, 10), 8)
        top_row = hbox(s=9)
        self.title_lab = label("Inbox", 15, T.TEXT_PRIMARY, 600)
        top_row.addWidget(self.title_lab)
        self.unread_lab = label("", 11, T.TEXT_DIM)
        top_row.addWidget(self.unread_lab)
        top_row.addStretch(1)
        self.compose_btn = button("＋ Compose", "primary", px=11)
        self.compose_btn.setFixedHeight(26)
        self.compose_btn.setToolTip("Write a new email")
        self.compose_btn.clicked.connect(lambda: state.open_compose())
        top_row.addWidget(self.compose_btn)
        hv.addLayout(top_row)

        # refresh rides the search row — a labeled button, not a bare glyph
        # (todo-fixes #9); the title row stays uncramped at 334px.
        search_row = hbox(s=8)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search mail…")
        f = self.search_box.font()
        f.setPixelSize(12)
        self.search_box.setFont(f)
        search_row.addWidget(self.search_box, 1)
        self.refresh_btn = button("↻ Refresh", "outline", px=11)
        self.refresh_btn.setFixedHeight(26)
        self.refresh_btn.setToolTip("Sync with Gmail now")
        self.refresh_btn.clicked.connect(state.refresh_inbox)
        search_row.addWidget(self.refresh_btn)
        self.suggest_btn = button("✨", "outline", px=13)
        self.suggest_btn.setFixedSize(28, 26)
        self.suggest_btn.setToolTip(
            "Suggest labels for unlabeled mail (runs the local model once)")
        self.suggest_btn.clicked.connect(self._suggest)
        search_row.addWidget(self.suggest_btn)
        hv.addLayout(search_row)

        self.status_lab = label("", 11, T.TEXT_DIM)
        hv.addWidget(self.status_lab)

        # filter chips: Inbox · Unread · one per user label (scoped DB re-query)
        chips_host = QWidget()
        self.chips_lay = FlowLayout(chips_host)
        hv.addWidget(chips_host)

        cv.addWidget(head)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {T.BORDER_SOFT};")
        cv.addWidget(sep)
        rows_host = QWidget()
        self.rows_lay = vbox(rows_host, (0, 0, 0, 0), 0)
        self.rows_lay.addStretch(1)
        cv.addWidget(scroll(rows_host), 1)
        root.addWidget(col)
        root.addWidget(vline(T.BORDER_SOFT))

        # ---- reading pane ----
        pane = QWidget()
        self.pane_lay = vbox(pane, (26, 20, 26, 20), 0)
        root.addWidget(scroll(pane), 1)

        # search box debounces 300ms before hitting the daemon
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(
            lambda: self.state.search_mails(self.search_box.text()))
        self.search_box.textChanged.connect(
            lambda _t: self._search_timer.start())

        # while the tab is visible, re-read the local mirror every 5 min so
        # mail the daemon's background Gmail poll brought in becomes visible
        # without a click — local read only, no extra Gmail traffic
        self._auto_requery = QTimer(self)
        self._auto_requery.setInterval(5 * 60 * 1000)
        self._auto_requery.timeout.connect(self.state.refresh_mails)

        state.mails_changed.connect(self.populate)
        self.populate()

    def showEvent(self, ev):
        # Entering the tab syncs with Gmail (debounced to 1/min in AppState);
        # inside the debounce window it still re-reads the local mirror, so
        # labels created while hidden appear without ↻ (todo-fixes #10d).
        super().showEvent(ev)
        self.state.sync_inbox()
        self._auto_requery.start()

    def hideEvent(self, ev):
        super().hideEvent(ev)
        self._auto_requery.stop()

    def populate(self):
        scope = self.state.mail_scope
        self.title_lab.setText(
            {"inbox": "Inbox", "unread": "Inbox", "sent": "Sent"}.get(scope, scope))
        self.unread_lab.setText(f"{self.state.unread_count()} unread")
        self.status_lab.setText(self._status_text())
        self._build_chips()
        clear_layout(self.rows_lay)
        if not self.state.mails:
            if not self.state.mail_connected:
                self.rows_lay.addWidget(empty_state(
                    "Gmail not connected", "run: lumen-google-auth"))
            else:
                self.rows_lay.addWidget(empty_state(
                    "No messages", "your inbox is clear"))
            self.rows_lay.addStretch(1)
            self._populate_pane()
            return
        for m in self.state.mails:
            selected = m["id"] == self.state.selected_mail
            row = ClickRow(lambda mid=m["id"]: self.state.select_mail(mid))
            row.setProperty("cls", "selrow")
            row.setProperty("sel", "true" if selected else "false")
            rl = hbox(row, (10, 10, 12, 10), 10)
            dot = Dot(7, T.ACCENT) if m["unread"] else Dot(7, "transparent", border=T.TEXT_FAINT)
            rl.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
            body = vbox(s=0)
            top = hbox(s=8)
            top.addWidget(ElideLabel(m["from"], 12, T.TEXT_PRIMARY if m["unread"] else T.TEXT_BODY_MUTED,
                                     600 if m["unread"] else 400), 1)
            top.addWidget(label(m["time"], 10, T.TEXT_DIM))
            body.addLayout(top)
            body.addSpacing(1)
            body.addWidget(ElideLabel(m["subj"], 12, T.TEXT_SECONDARY))
            body.addSpacing(2)
            body.addWidget(ElideLabel(m["preview"], 11, T.TEXT_DIM, sans=True))
            names = m.get("label_names") or []
            sug = self.state.mail_suggestions.get(m["id"])
            if names or sug:
                pills = hbox(s=4)
                for n in names[:3]:
                    c = T.label_color(n)
                    pills.addWidget(Chip(n, c, c, px=9, radius=7, hpad=6, vpad=1))
                if sug:
                    pills.addWidget(ClickChip(
                        f"＋ {sug}", T.ACCENT, T.ACCENT, px=9,
                        on_click=lambda mid=m["id"]: self.state.apply_suggestion(mid),
                        tooltip="Suggested label — click to file it (leaves the inbox)"))
                pills.addStretch(1)
                body.addSpacing(3)
                body.addLayout(pills)
            rl.addLayout(body, 1)
            self.rows_lay.addWidget(row)
            sep = QFrame()
            sep.setFixedHeight(1)
            sep.setStyleSheet(f"background: {T.BORDER_FAINT};")
            self.rows_lay.addWidget(sep)
        self.rows_lay.addStretch(1)
        self._populate_pane()

    def _build_chips(self):
        # No "All": each chip is its own inbox — "Inbox" is unlabeled INBOX
        # mail, every label shows only its own (design 2026-07-16). "Sent"
        # is the outbound scope switcher (todo-fixes #9).
        clear_layout(self.chips_lay)
        for name, scope in ([("Inbox", "inbox"), ("Unread", "unread"),
                             ("Sent", "sent")]
                            + [(l, l) for l in self.state.mail_labels]):
            color = (T.TEXT_SECONDARY if scope in ("inbox", "unread", "sent")
                     else T.label_color(name))
            sel = self.state.mail_scope == scope
            self.chips_lay.addWidget(ClickChip(
                name, color, color, bg=(color + "1f" if sel else None),
                on_click=lambda s=scope: self.state.set_mail_scope(s)))

    def _suggest(self):
        self.suggest_btn.setEnabled(False)
        self.state.suggest_labels(
            lambda _r: self.suggest_btn.setEnabled(True))

    def _status_text(self) -> str:
        if not self.state.mail_connected:
            return "Gmail not connected — see setup"
        if self.state.mail_syncing:
            return f"syncing — {self.state.mail_total} so far"
        return f"synced {self.state.mail_last_sync or '—'}"

    def _reply(self, m: dict):
        subj = m["subj"] if m["subj"].lower().startswith("re:") else f"Re: {m['subj']}"
        self.state.open_compose({
            "to": [m["from_addr"]] if m.get("from_addr") else [],
            "subject": subj, "reply_to": m["id"]})

    def _populate_pane(self):
        clear_layout(self.pane_lay)
        m = self.state.sel_mail()
        if m is None:
            self.pane_lay.addWidget(label("No message selected", 14, T.TEXT_DIM))
            self.pane_lay.addStretch(1)
            return
        subj = label(m["subj"], 18, T.TEXT_PRIMARY, 600, wrap=True)
        self.pane_lay.addWidget(subj)
        self.pane_lay.addSpacing(10)

        sender = QWidget()
        sl = hbox(sender, (0, 0, 0, 14), 11)
        initial = next((ch for ch in m["from"] if ch.isalpha()), "?").upper()
        avatar = QLabel(initial)
        avatar.setFixedSize(34, 34)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setFont(font(14, 600))
        avatar.setStyleSheet(
            f"background: {T.ACCENT_SOFT_QSS}; border: 1px solid {T.ACCENT}; "
            f"border-radius: 17px; color: {T.ACCENT};")
        sl.addWidget(avatar)
        who = vbox(s=0)
        who.addWidget(label(m["from"], 13, T.TEXT_PRIMARY, 500))
        who.addWidget(label(f"to me · {m['date']}", 11, T.TEXT_DIM))
        sl.addLayout(who, 1)
        self.reply_btn = button("↳ Reply", "primary", px=11)
        self.reply_btn.setFixedHeight(29)
        self.reply_btn.clicked.connect(lambda: self._reply(m))
        sl.addWidget(self.reply_btn)
        self.archive_btn = button("Archive", "outline", px=11)
        self.archive_btn.setFixedHeight(29)
        self.archive_btn.clicked.connect(lambda: self.state.archive_mail(m["id"]))
        sl.addWidget(self.archive_btn)
        self.read_btn = button("Mark read" if m["unread"] else "Mark unread", "outline", px=11)
        self.read_btn.setFixedHeight(29)
        self.read_btn.clicked.connect(
            lambda: self.state.set_mail_read(m["id"], m["unread"]))
        sl.addWidget(self.read_btn)
        self.rule_btn = button("⚑ Rule", "outline", px=11)
        self.rule_btn.setFixedHeight(29)
        self.rule_btn.setToolTip("Always label mail like this…")
        self.rule_btn.clicked.connect(lambda: self.state.open_rule_editor(
            {"from_addrs": [m["from_addr"]] if m.get("from_addr") else []}))
        sl.addWidget(self.rule_btn)
        self.pane_lay.addWidget(sender)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {T.BORDER_SOFT};")
        self.pane_lay.addWidget(sep)

        names = m.get("label_names") or []
        if names:
            pr = hbox(m=(0, 10, 0, 0), s=5)
            for n in names:
                c = T.label_color(n)
                pr.addWidget(Chip(n, c, c, px=10, radius=7, hpad=7, vpad=2))
            pr.addStretch(1)
            self.pane_lay.addLayout(pr)

        attachments = m.get("attachments") or []
        if attachments:
            self.pane_lay.addWidget(
                label("📎 " + ", ".join(attachments), 11, T.TEXT_DIM, sans=True))

        # HTML when the mirror has it; plain text otherwise (older rows get
        # their HTML lazily via emails.get the first time they're opened).
        body = (HtmlBody(m["body_html"]) if m.get("body_html")
                else label(m["body"], 13, T.TEXT_PRIMARY, sans=True, wrap=True))
        bw = QWidget()
        bl = vbox(bw, (0, 16, 0, 16), 0)
        bl.addWidget(body)
        self.pane_lay.addWidget(bw)
        self.pane_lay.addStretch(1)
