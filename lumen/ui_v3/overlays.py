"""In-window overlays: confirm, draft composer, event composer, toast.

These sit as full-rect children of the main window (not QDialogs) so they can
dim the app behind them exactly as the mock does, and so a confirm arriving over
IPC while the hotkey overlay is up still lands somewhere visible.

Every one of them gates a write. The confirm overlay in particular is the last
stop before Lumen touches a connected account, so its Cancel is the default and
Escape always dismisses.
"""
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QLineEdit, QPlainTextEdit, QWidget,
)

from . import theme as T
from .widgets import (
    Chip, ClickLabel, button, clear_layout, eyebrow, font, hbox, hline, label,
    scroll, shadow, vbox,
)


class _Scrim(QWidget):
    """Dimmed backdrop that centers one card and closes on an outside click."""

    def __init__(self, parent, top_align: bool = False, dismissable: bool = True):
        super().__init__(parent)
        self.setObjectName("scrimLaunch" if top_align else "scrim")
        # Without WA_StyledBackground a plain QWidget ignores its stylesheet
        # background, and the modal backdrop would not dim anything.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._dismissable = dismissable
        self._on_dismiss = None
        lay = vbox(self, (0, 110 if top_align else 0, 0, 0), 0)
        if not top_align:
            lay.addStretch(1)     # vertically centered; top_align pins near the top
        self.row = hbox(m=(0, 0, 0, 0), s=0)
        self.row.addStretch(1)
        lay.addLayout(self.row)
        lay.addStretch(1)
        self.hide()

    def set_card(self, card: QWidget):
        while self.row.count() > 1:
            item = self.row.takeAt(1)
            if item.widget():
                item.widget().setParent(None)
        self.row.addWidget(card, 0, Qt.AlignmentFlag.AlignTop)
        self.row.addStretch(1)

    def mousePressEvent(self, ev):
        if self._dismissable and self._on_dismiss:
            self._on_dismiss()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape and self._on_dismiss:
            self._on_dismiss()
        else:
            super().keyPressEvent(ev)

    def pop(self):
        self.setGeometry(self.parent().rect())
        self.show()
        self.raise_()
        self.setFocus()


class _Card(QFrame):
    """The dialog body — swallows clicks so they don't reach the scrim."""

    def mousePressEvent(self, ev):
        ev.accept()


class ConfirmOverlay(_Scrim):
    """The write-confirmation gate. Payload may carry a `confirm_id`, in which
    case a daemon connection is blocked awaiting the answer — so every path out
    of here, including Escape, must answer it."""

    def __init__(self, parent):
        super().__init__(parent, dismissable=False)
        self._callback = None
        self._payload: dict = {}
        self._on_dismiss = lambda: self._answer(False)

        self.card = _Card()
        self.card.setProperty("role", "dialog-accent")
        self.card.setFixedWidth(T.CONFIRM_W)
        shadow(self.card, 60, 24, 80)
        self.lay = vbox(self.card, (0, 0, 0, 0), 0)
        self.set_card(self.card)

    def open(self, payload: dict, callback):
        self._payload, self._callback = payload, callback
        clear_layout(self.lay)

        head = hbox(m=(20, 18, 20, 14), s=12)
        icon = label(payload.get("icon", "▲"), 16, T.ACCENT)
        icon.setFixedSize(T.sc(32), T.sc(32))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        r, g, b = (int(T.ACCENT[i:i + 2], 16) for i in (1, 3, 5))
        icon.setStyleSheet(f"background: rgba({r},{g},{b},0.10);"
                           f"border: 1px solid {T.ACCENT}; border-radius: 6px;")
        head.addWidget(icon)
        col = vbox(m=(0, 0, 0, 0), s=2)
        col.addWidget(label(payload.get("title", "Confirm"), 19,
                            T.TEXT_PRIMARY, 500))
        col.addWidget(label("⚠ WRITE ACTION · TOUCHES A CONNECTED ACCOUNT", 9.5,
                            T.WARN, mono=True, ls=0.8))
        head.addLayout(col, 1)
        self.lay.addLayout(head)
        self.lay.addWidget(hline(T.BORDER_MED))

        body = vbox(m=(20, 16, 20, 16), s=14)
        intro = payload.get("intro", "")
        if intro:
            body.addWidget(label(intro, 13.5, T.TEXT_SECONDARY, wrap=True))
        rows = payload.get("rows") or []
        if rows:
            panel = QFrame()
            panel.setProperty("role", "panel")
            pv = vbox(panel, (14, 12, 14, 12), 0)
            for k, v in rows:
                r = hbox(m=(0, 6, 0, 6), s=14)
                kl = label(str(k), 10.5, T.TEXT_FAINT, mono=True)
                kl.setFixedWidth(T.sc(66))
                kl.setAlignment(Qt.AlignmentFlag.AlignRight
                                | Qt.AlignmentFlag.AlignTop)
                r.addWidget(kl)
                r.addWidget(label(str(v), 13.5, T.TEXT_PRIMARY, wrap=True), 1)
                pv.addLayout(r)
            body.addWidget(panel)

        # Optional checkbox (rule-create backfill) rides back with the answer.
        self.check = None
        if payload.get("check_label"):
            from PyQt6.QtWidgets import QCheckBox
            self.check = QCheckBox(payload["check_label"])
            self.check.setChecked(bool(payload.get("check_default")))
            body.addWidget(self.check)
        self.lay.addLayout(body)

        actions = hbox(m=(20, 14, 20, 18), s=11)
        cancel = button("Cancel", "ghost", px=15, height=40)
        cancel.clicked.connect(lambda: self._answer(False))
        actions.addWidget(cancel, 1)
        ok = button(payload.get("confirm_label", "Confirm"), "primary", px=15,
                    height=40)
        ok.clicked.connect(lambda: self._answer(True))
        ok.setDefault(True)
        actions.addWidget(ok, 1)
        self.lay.addLayout(actions)

        self.pop()

    def _answer(self, approved: bool):
        self.hide()
        payload = dict(self._payload)
        if self.check is not None:
            payload["check_state"] = self.check.isChecked()
        if self._callback:
            self._callback(approved, payload)


class ComposeOverlay(_Scrim):
    """Draft composer with its own inline "Ask Lumen" line for revisions."""

    def __init__(self, parent, state):
        super().__init__(parent)
        self.state = state
        self._compose_id = None
        self._reply_to = None
        self._on_dismiss = self.close_it

        self.card = _Card()
        self.card.setProperty("role", "dialog")
        self.card.setFixedWidth(T.DRAFT_W)
        shadow(self.card, 60, 24, 76)
        v = vbox(self.card, (0, 0, 0, 0), 0)

        head = hbox(m=(20, 15, 20, 15), s=10)
        head.addWidget(label("New email", 15, T.TEXT_PRIMARY, 600))
        head.addStretch(1)
        head.addWidget(label("VIA GMAIL · CONFIRM BEFORE SEND", 9.5,
                             T.TEXT_FAINT, mono=True, ls=1))
        v.addLayout(head)
        v.addWidget(hline(T.BORDER_MED))

        body = vbox(m=(20, 14, 20, 14), s=9)
        self.to = self._field(body, "To")
        self.subject = self._field(body, "Subject", weight=600)
        self.body_edit = QPlainTextEdit()
        self.body_edit.setPlaceholderText("Write your message…")
        self.body_edit.setFixedHeight(T.sc(160))
        body.addWidget(self.body_edit)
        v.addLayout(body)

        actions = hbox(m=(20, 4, 20, 14), s=11)
        discard = button("Discard", "ghost", px=13.5, height=36)
        discard.clicked.connect(self.close_it)
        actions.addWidget(discard)
        actions.addStretch(1)
        send = button("Review & send →", "primary", px=13.5, height=36)
        send.clicked.connect(self._send)
        actions.addWidget(send)
        v.addLayout(actions)

        self.hint = QFrame()
        self.hint.setProperty("role", "panel")
        hv = vbox(self.hint, (13, 10, 13, 10), 0)
        self.hint_text = label("", 13, T.TEXT_BODY, wrap=True)
        hv.addWidget(self.hint_text)
        self.hint.hide()
        hwrap = hbox(m=(20, 0, 20, 10), s=0)
        hwrap.addWidget(self.hint)
        v.addLayout(hwrap)

        foot = QFrame()
        foot.setProperty("role", "dialogfoot")
        fv = hbox(foot, (20, 10, 20, 10), 9)
        fv.addWidget(label("❯", 12, T.ACCENT, mono=True))
        self.ask = QLineEdit()
        self.ask.setProperty("cls", "bare")
        self.ask.setPlaceholderText(
            "Ask Lumen — “make it more formal”, “suggest a subject line”…")
        self.ask.setFont(font(13))
        self.ask.returnPressed.connect(self._revise)
        fv.addWidget(self.ask, 1)
        fv.addWidget(label("↵", 9, T.TEXT_FAINT, mono=True))
        v.addWidget(foot)
        self.set_card(self.card)

    def _field(self, parent_lay, name: str, weight: int = 400) -> QLineEdit:
        row = hbox(m=(0, 0, 0, 8), s=10)
        lab = label(name, 10.5, T.TEXT_FAINT, mono=True)
        lab.setFixedWidth(T.sc(56))
        lab.setAlignment(Qt.AlignmentFlag.AlignRight
                         | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(lab)
        field = QLineEdit()
        field.setProperty("cls", "bare")
        field.setFont(font(14, weight))
        row.addWidget(field, 1)
        parent_lay.addLayout(row)
        parent_lay.addWidget(hline(T.BORDER_FAINT))
        return field

    def open(self, payload: dict):
        self._compose_id = payload.get("compose_id")
        self._reply_to = payload.get("reply_to")
        # `to` arrives as a list from the daemon's compose_request (find-then-
        # send prefills a recipient) and as a plain string from the mail
        # screen's Compose/Reply. setText only accepts a str, so normalize —
        # passing the list straight through crashed the app (todo-fixes #26).
        to = payload.get("to") or ""
        if isinstance(to, list):
            to = ", ".join(to)
        self.to.setText(to)
        self.subject.setText(payload.get("subject", ""))
        self.body_edit.setPlainText(payload.get("body", ""))
        self.hint.hide()
        self.ask.clear()
        self.pop()
        # A reply arrives with the recipient filled in — land in the body.
        (self.body_edit if to else self.to).setFocus()

    def fields(self) -> dict:
        # The daemon's send path expects address lists (it iterates `to`), so
        # split the field rather than handing it a bare string — a string would
        # be walked character by character and rejected as invalid addresses.
        to = [a.strip() for a in self.to.text().split(",") if a.strip()]
        return {"to": to, "cc": [], "bcc": [],
                "subject": self.subject.text().strip(),
                "body": self.body_edit.toPlainText(),
                "reply_to": self._reply_to}

    def close_it(self):
        self.hide()
        if self._compose_id is not None:
            # A chat-driven compose has a blocked daemon connection behind it.
            self.state.respond_compose(self._compose_id, None)
            self._compose_id = None

    def _send(self):
        fields = self.fields()
        if not fields["to"]:
            self.to.setFocus()
            return
        self.hide()
        if self._compose_id is not None:
            self.state.respond_compose(self._compose_id, fields)
            self._compose_id = None
            return
        self.state.send_email(fields, self._sent)

    def _sent(self, result):
        msg = (result or {}).get("message", "Sent")
        self.state.toast_requested.emit(("✓ " if (result or {}).get("ok") else "")
                                        + msg)

    def _revise(self):
        instruction = self.ask.text().strip()
        if not instruction:
            return
        self.ask.clear()
        self.hint.show()
        self.hint_text.setText("Thinking…")

        def done(result):
            if not (result or {}).get("ok"):
                self.hint_text.setText((result or {}).get("message",
                                                          "Revision failed."))
                return
            if result.get("body"):
                self.body_edit.setPlainText(result["body"])
            if result.get("subject"):
                self.subject.setText(result["subject"])
            self.hint_text.setText(result.get("note", "Updated the draft."))

        payload = dict(self.fields())
        payload["instruction"] = instruction
        self.state.revise_email(payload, done)


class EventOverlay(_Scrim):
    """New-event composer. Times stay free text; the daemon validates and the
    confirm overlay still gates the actual write."""

    def __init__(self, parent, state):
        super().__init__(parent)
        self.state = state
        self._on_dismiss = self.hide

        self.card = _Card()
        self.card.setProperty("role", "dialog")
        self.card.setFixedWidth(T.EVENT_W)
        shadow(self.card, 60, 24, 76)
        v = vbox(self.card, (0, 0, 0, 0), 0)

        head = hbox(m=(20, 15, 20, 15), s=10)
        head.addWidget(label("New event", 15, T.TEXT_PRIMARY, 600))
        head.addStretch(1)
        head.addWidget(label("GOOGLE CALENDAR · CONFIRM FIRST", 9.5,
                             T.TEXT_FAINT, mono=True, ls=1))
        v.addLayout(head)
        v.addWidget(hline(T.BORDER_MED))

        body = vbox(m=(20, 16, 20, 16), s=12)
        self.title = QLineEdit()
        self.title.setPlaceholderText("Event title")
        self.title.setFont(font(15, 600))
        body.addWidget(self.title)

        row = hbox(s=9)
        self.date = self._labeled(row, "Date", 1.2)
        self.start = self._labeled(row, "Start", 0.8)
        self.end = self._labeled(row, "End", 0.8)
        body.addLayout(row)

        loc_col = vbox(s=5)
        loc_col.addWidget(eyebrow("Location (optional)", px=9.5, ls=1))
        self.location = QLineEdit()
        loc_col.addWidget(self.location)
        body.addLayout(loc_col)
        v.addLayout(body)

        actions = hbox(m=(20, 4, 20, 14), s=11)
        cancel = button("Cancel", "ghost", px=13.5, height=36)
        cancel.clicked.connect(self.hide)
        actions.addWidget(cancel)
        actions.addStretch(1)
        create = button("Review & create →", "primary", px=13.5, height=36)
        create.clicked.connect(self._create)
        actions.addWidget(create)
        v.addLayout(actions)
        self.set_card(self.card)

    def _labeled(self, row, name: str, stretch: float) -> QLineEdit:
        col = vbox(s=5)
        col.addWidget(eyebrow(name, px=9.5, ls=1))
        field = QLineEdit()
        field.setFont(font(13.5))
        col.addWidget(field)
        row.addLayout(col, int(stretch * 10))
        return field

    def open(self, payload: dict):
        self.title.clear()
        self.date.setText(payload.get("date", ""))
        self.start.setText(payload.get("start", ""))
        self.start.setPlaceholderText("14:00")
        self.end.setText(payload.get("end", ""))
        self.end.setPlaceholderText("15:00")
        self.location.clear()
        self.pop()
        self.title.setFocus()

    def _create(self):
        title = self.title.text().strip()
        if not title:
            self.title.setFocus()
            return
        day = self.date.text().strip()
        self.hide()
        self.state.create_event({
            "title": title,
            "start": f"{day}T{self.start.text().strip()}",
            "end": f"{day}T{self.end.text().strip()}",
            "location": self.location.text().strip() or None})


class Toast(QFrame):
    """Bottom-centered transient confirmation."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("toast")
        lay = hbox(self, (18, 10, 18, 10), 0)
        self.text = label("", 15, "#e9e0cb")
        lay.addWidget(self.text)
        shadow(self, 36, 12, 72)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(2600)
        self._timer.timeout.connect(self.hide)

    def pop(self, message: str):
        if not message:
            return
        self.text.setText(message)
        self.adjustSize()
        self.reposition()
        self.show()
        self.raise_()
        self._timer.start()

    def reposition(self):
        p = self.parent()
        if p is None:
            return
        self.adjustSize()
        self.move((p.width() - self.width()) // 2, p.height() - self.height() - 22)
