"""Mail screen: 334px message list | reading pane."""
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QFrame, QLabel, QLineEdit, QWidget

from .. import theme as T
from ..state import AppState
from ..widgets import (
    ClickRow, Dot, ElideLabel, button, clear_layout, font, hbox, label, qcolor,
    scroll, vbox, vline,
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
        top_row.addWidget(label("Inbox", 15, T.TEXT_PRIMARY, 600))
        self.unread_lab = label("", 11, T.TEXT_DIM)
        top_row.addWidget(self.unread_lab)
        top_row.addStretch(1)
        self.compose_btn = button("＋ Compose", "primary", px=11)
        self.compose_btn.setFixedHeight(26)
        self.compose_btn.setToolTip("Write a new email")
        self.compose_btn.clicked.connect(lambda: state.open_compose())
        top_row.addWidget(self.compose_btn)
        refresh_btn = button("↻", "outline", px=13)
        refresh_btn.setFixedSize(28, 26)
        refresh_btn.setToolTip("Refresh inbox")
        refresh_btn.clicked.connect(state.refresh_inbox)
        top_row.addWidget(refresh_btn)
        hv.addLayout(top_row)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search mail…")
        f = self.search_box.font()
        f.setPixelSize(12)
        self.search_box.setFont(f)
        hv.addWidget(self.search_box)

        self.status_lab = label("", 11, T.TEXT_DIM)
        hv.addWidget(self.status_lab)

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

        state.mails_changed.connect(self.populate)
        self.populate()

    def populate(self):
        self.unread_lab.setText(f"{self.state.unread_count()} unread")
        self.status_lab.setText(self._status_text())
        clear_layout(self.rows_lay)
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
            rl.addLayout(body, 1)
            self.rows_lay.addWidget(row)
            sep = QFrame()
            sep.setFixedHeight(1)
            sep.setStyleSheet(f"background: {T.BORDER_FAINT};")
            self.rows_lay.addWidget(sep)
        self.rows_lay.addStretch(1)
        self._populate_pane()

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
        self.pane_lay.addWidget(sender)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {T.BORDER_SOFT};")
        self.pane_lay.addWidget(sep)

        attachments = m.get("attachments") or []
        if attachments:
            self.pane_lay.addWidget(
                label("📎 " + ", ".join(attachments), 11, T.TEXT_DIM, sans=True))

        body = label(m["body"], 13, T.TEXT_PRIMARY, sans=True, wrap=True)
        bw = QWidget()
        bl = vbox(bw, (0, 16, 0, 16), 0)
        bl.addWidget(body)
        self.pane_lay.addWidget(bw)
        self.pane_lay.addStretch(1)
