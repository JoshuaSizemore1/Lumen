"""Mail screen: 334px message list | reading pane."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QLabel, QWidget

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
        hv = hbox(head, (16, 16, 16, 12), 9)
        hv.addWidget(label("Inbox", 15, T.TEXT_PRIMARY, 600))
        self.unread_lab = label("", 11, T.TEXT_DIM)
        hv.addWidget(self.unread_lab)
        hv.addStretch(1)
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

        state.mails_changed.connect(self.populate)
        self.populate()

    def populate(self):
        self.unread_lab.setText(f"{self.state.unread_count()} unread")
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

    def _populate_pane(self):
        clear_layout(self.pane_lay)
        m = self.state.sel_mail()
        if m is None:
            self.pane_lay.addWidget(label("No messages yet", 14, T.TEXT_DIM))
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
        reply = button("↳ Reply", "primary", px=11)
        reply.setFixedHeight(29)
        reply.clicked.connect(self.state.reply_confirm)
        sl.addWidget(reply)
        archive = button("Archive", "outline", px=11)
        archive.setFixedHeight(29)
        archive.clicked.connect(self._on_archive)
        sl.addWidget(archive)
        self.pane_lay.addWidget(sender)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {T.BORDER_SOFT};")
        self.pane_lay.addWidget(sep)

        body = label(m["body"], 13, T.TEXT_PRIMARY, sans=True, wrap=True)
        bw = QWidget()
        bl = vbox(bw, (0, 16, 0, 16), 0)
        bl.addWidget(body)
        self.pane_lay.addWidget(bw)
        self.pane_lay.addStretch(1)

    def _on_archive(self):
        # TODO: wire to daemon archive action
        self.state.toast_requested.emit("✓ Archived (stub)")
