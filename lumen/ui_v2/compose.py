"""Compose-email overlay: the editable counterpart of the confirm overlay.

One dialog serves every path — the Mail screen's Compose/Reply buttons open it
locally and Send fires the emails.send one-shot; a chat-driven draft arrives
with a compose_id and Send/Cancel answer the daemon's waiting turn instead.
Clicking Send IS the write confirmation: the user is looking at the exact
recipients and body, so there is no second dialog."""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QFrame, QLabel, QLineEdit, QTextEdit, QWidget

from . import theme as T
from .state import AppState
from .widgets import CompositeButton, button, font, hbox, hline, label, qcolor, vbox


class ComposeDialog(QWidget):
    """Dimmed overlay covering the window with a centered 560px compose card."""

    def __init__(self, parent: QWidget, state: AppState):
        super().__init__(parent)
        self.state = state
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._compose_id = None
        self._reply_to = None
        self._busy_revise = False
        # a daemon error means a pending revise callback will never fire
        state.status_requested.connect(lambda _m: self._set_revise_busy(False))

        lay = hbox(self, (20, 20, 20, 20), 0)
        lay.addStretch(1)
        card_wrap = vbox(s=0)
        card_wrap.addStretch(1)
        card = QFrame()
        card.setProperty("cls", "dialog")
        card.setFixedWidth(560)
        cl = vbox(card, (0, 0, 0, 0), 0)

        head = QWidget()
        hl = hbox(head, (18, 16, 18, 12), 11)
        icon = QLabel("✉")
        icon.setFixedSize(30, 30)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFont(font(15))
        icon.setStyleSheet(f"background: {T.ACCENT_SOFT_QSS}; border: 1px solid "
                           f"{T.ACCENT}; border-radius: 7px; color: {T.ACCENT};")
        hl.addWidget(icon)
        tv = vbox(s=1)
        self.title_lab = label("Compose email", 14, T.TEXT_PRIMARY, 600)
        tv.addWidget(self.title_lab)
        tv.addWidget(label("⚠ WRITE ACTION · SENDS FROM YOUR GMAIL ACCOUNT",
                           10, T.WARN, ls=0.4))
        hl.addLayout(tv, 1)
        cl.addWidget(head)
        cl.addWidget(hline(T.BORDER_MED))

        body_w = QWidget()
        bl = vbox(body_w, (18, 14, 18, 10), 8)
        self.to_edit = self._addr_row(bl, "To")
        self.cc_edit = self._addr_row(bl, "Cc")
        self.bcc_edit = self._addr_row(bl, "Bcc")
        self.subject_edit = self._addr_row(bl, "Subject")
        self.body_edit = QTextEdit()
        self.body_edit.setMinimumHeight(180)
        self.body_edit.setAcceptRichText(False)
        f = self.body_edit.font()
        f.setPixelSize(13)
        self.body_edit.setFont(f)
        bl.addWidget(self.body_edit)

        rev = hbox(s=8)
        self.revise_edit = QLineEdit()
        self.revise_edit.setPlaceholderText(
            "Ask Lumen to revise — e.g. shorter, more formal")
        rev.addWidget(self.revise_edit, 1)
        self.revise_btn = button("✦ Revise", "outline", px=11)
        self.revise_btn.setFixedHeight(30)
        self.revise_btn.clicked.connect(self._revise)
        self.revise_edit.returnPressed.connect(self._revise)
        rev.addWidget(self.revise_btn)
        bl.addLayout(rev)

        self.err_lab = label("", 12, T.WARN, sans=True, wrap=True)
        self.err_lab.hide()
        bl.addWidget(self.err_lab)
        cl.addWidget(body_w)

        foot = QWidget()
        fl = hbox(foot, (18, 8, 18, 16), 10)
        cancel = CompositeButton("cancel", [label("Cancel", 13, T.TEXT_SECONDARY),
                                            label("esc", 10, T.TEXT_DIM)], height=38)
        cancel.clicked.connect(self._cancel)
        fl.addWidget(cancel, 10)
        send = CompositeButton("confirm", [label("Send", 13, T.ACCENT_ON, 600)],
                               height=38)
        send.clicked.connect(self._send)
        fl.addWidget(send, 14)
        cl.addWidget(foot)

        card_wrap.addWidget(card)
        card_wrap.addStretch(1)
        lay.addLayout(card_wrap)
        lay.addStretch(1)
        self.hide()

    def _addr_row(self, lay, name: str) -> QLineEdit:
        row = hbox(s=10)
        lab = label(name, 11, T.TEXT_DIM)
        lab.setFixedWidth(52)
        lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(lab)
        edit = QLineEdit()
        row.addWidget(edit, 1)
        lay.addLayout(row)
        return edit

    # ---- lifecycle --------------------------------------------------------
    def open(self, payload: dict):
        """Reset every field from the payload; missing keys mean empty. A
        compose_id marks a chat-driven draft whose turn awaits our answer."""
        self._compose_id = payload.get("compose_id")
        self._reply_to = payload.get("reply_to")
        self.title_lab.setText("Reply" if self._reply_to else "Compose email")
        self.to_edit.setText(", ".join(payload.get("to") or []))
        self.cc_edit.setText(", ".join(payload.get("cc") or []))
        self.bcc_edit.setText(", ".join(payload.get("bcc") or []))
        self.subject_edit.setText(payload.get("subject") or "")
        self.body_edit.setPlainText(payload.get("body") or "")
        self.revise_edit.clear()
        self.err_lab.hide()
        self._set_revise_busy(False)
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        (self.body_edit if payload.get("to") else self.to_edit).setFocus()

    def _fields(self) -> dict:
        def split(edit):
            return [a.strip() for a in edit.text().split(",") if a.strip()]
        return {"to": split(self.to_edit), "cc": split(self.cc_edit),
                "bcc": split(self.bcc_edit),
                "subject": self.subject_edit.text().strip(),
                "body": self.body_edit.toPlainText().strip(),
                "reply_to": self._reply_to}

    # ---- actions ------------------------------------------------------------
    def _send(self):
        if self._compose_id is not None:
            # chat-driven: the daemon sends and reports in the chat turn
            self.state.respond_compose(self._compose_id, self._fields())
            self.hide()
            return
        self.state.send_email(self._fields(), self._sent)

    def _sent(self, result: dict):
        if result.get("ok"):
            self.hide()
            self.state.toast_requested.emit("✓ " + (result.get("message") or "Sent."))
        else:                    # failure keeps the draft — nothing typed is lost
            self.err_lab.setText(result.get("message") or "Sending failed.")
            self.err_lab.show()

    def _cancel(self):
        if self._compose_id is not None:
            self.state.respond_compose(self._compose_id, None)
        self.hide()

    def _revise(self):
        instruction = self.revise_edit.text().strip()
        if not instruction or self._busy_revise:
            return
        self._set_revise_busy(True)
        self.state.revise_email(
            {"subject": self.subject_edit.text().strip(),
             "body": self.body_edit.toPlainText().strip(),
             "instruction": instruction},
            self._revised)

    def _revised(self, result: dict):
        self._set_revise_busy(False)
        if "body" in result:     # recipients are never touched by revision
            self.subject_edit.setText(result.get("subject") or "")
            self.body_edit.setPlainText(result["body"])
            self.revise_edit.clear()

    def _set_revise_busy(self, busy: bool):
        self._busy_revise = busy
        self.revise_btn.setEnabled(not busy)
        self.revise_btn.setText("revising…" if busy else "✦ Revise")

    # ---- chrome -------------------------------------------------------------
    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self._cancel()
        else:
            super().keyPressEvent(ev)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), qcolor("#06070b", int(0.72 * 255)))
