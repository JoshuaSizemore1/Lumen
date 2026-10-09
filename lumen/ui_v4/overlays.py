"""In-window modal overlays: confirm, compose, event, rule editor, suggested-
label review, command palette — plus the toast.

Each is a full-rect child of the main window (not a QDialog) so it can dim the
app behind it, and so a confirm arriving over IPC while only the hotkey
launcher is up still lands somewhere visible. All of them: scrim, 12-radius
card with the overlay shadow, Esc cancels, Tab stays inside the card, and focus
goes back where it was on close.

Every one of them gates a write. The confirm overlay is the last stop before
Lumen touches a connected account: every way out of it answers the daemon.
"""
from PyQt6 import sip
from PyQt6.QtCore import (
    QEasingCurve, QEvent, QParallelAnimationGroup, QPoint, QPropertyAnimation,
    QSize, Qt, QTimer,
)
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QFrame, QGraphicsOpacityEffect, QLineEdit,
    QLabel, QListWidget, QListWidgetItem, QPushButton, QScrollArea, QWidget,
)

from . import icons
from . import theme as T
from .components import (
    Avatar, Badge, Button, ClickRow, Divider, ElideLabel, FlowLayout, Heading,
    IconButton, IconLabel, Kbd, Label, MailBody, Panel, ScrollArea, TagChip,
    TextArea, TextField, TypingDots, EmptyState, clear_layout, fire_on_next_tick,
    hbox, shadow, vbox,
)

MARGIN = T.S6          # min gap between a card and the window edge


class _Card(QFrame):
    """The dialog body — swallows clicks so they don't reach the scrim."""

    def mousePressEvent(self, ev):
        ev.accept()


def _dialog_head(title: str, caption: str = "", close=None):
    """(layout, heading) for a dialog's title row."""
    head = hbox(m=(T.S6, T.S5, T.S5 if close else T.S6, T.S4), s=T.S3)
    col = vbox(s=2)
    h = Heading(title, 3, wrap=False)
    col.addWidget(h)
    if caption:
        col.addWidget(Label(caption, "caption"))
    head.addLayout(col, 1)
    if close is not None:
        head.addWidget(IconButton("x", "Close", on_click=close), 0,
                       Qt.AlignmentFlag.AlignTop)
    return head, h


def _dialog_foot():
    """(frame, hbox) for the button row."""
    foot = QFrame()
    foot.setProperty("role", "dialog-foot")
    return foot, hbox(foot, (T.S6, T.S4, T.S6, T.S4), T.S3)


class _Overlay(QWidget):
    CARD_W = 480
    CARD_H = 0            # 0 = height follows content
    CARD_NAME = "dialog"

    def __init__(self, parent, dismissable: bool = True, top: bool = False):
        super().__init__(parent)
        self.setObjectName("scrim")
        # A plain QWidget ignores its stylesheet background without this.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._dismissable = dismissable
        self._prev_focus = None

        self.card = _Card()
        self.card.setObjectName(self.CARD_NAME)
        shadow(self.card, "overlay")

        lay = vbox(self, (MARGIN, MARGIN, MARGIN, MARGIN), 0)
        if top:
            lay.addSpacing(72)
        else:
            lay.addStretch(1)
        row = hbox(s=0)
        row.addStretch(1)
        row.addWidget(self.card)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1 if not top else 3)

        esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        esc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        esc.activated.connect(self.cancel)
        parent.installEventFilter(self)
        self.hide()

    # ---- open / close -----------------------------------------------------
    def pop(self):
        if not self.isVisible():
            self._prev_focus = QApplication.focusWidget()
        self._fit()
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.setFocus()

    def close_overlay(self):
        self.hide()
        prev, self._prev_focus = self._prev_focus, None
        if prev is not None and not sip.isdeleted(prev) and prev.isVisible():
            prev.setFocus(Qt.FocusReason.OtherFocusReason)

    def cancel(self):
        """Esc / outside click. Subclasses answer whatever is waiting."""
        self.close_overlay()

    def _fit(self):
        p = self.parentWidget()
        self.card.setFixedWidth(max(320, min(self.CARD_W, p.width() - 2 * MARGIN)))
        avail = max(240, p.height() - 2 * MARGIN)
        if self.CARD_H:
            self.card.setFixedHeight(min(self.CARD_H, avail))
        else:
            self.card.setMaximumHeight(avail)

    def eventFilter(self, obj, ev):
        if (obj is self.parentWidget() and ev.type() == QEvent.Type.Resize
                and self.isVisible()):
            self._fit()
            self.setGeometry(obj.rect())
        return False

    def mousePressEvent(self, ev):
        if self._dismissable and not self.card.geometry().contains(
                ev.position().toPoint()):
            self.cancel()
        ev.accept()

    # ---- focus trap -------------------------------------------------------
    def _focusables(self) -> list:
        out, w, guard = [], self.card.nextInFocusChain(), 0
        while w is not None and w is not self.card and guard < 4000:
            guard += 1
            if (self.card.isAncestorOf(w) and w.isVisible() and w.isEnabled()
                    and not isinstance(w, QScrollArea)
                    and w.focusPolicy().value & Qt.FocusPolicy.TabFocus.value):
                out.append(w)
            w = w.nextInFocusChain()
        return out

    def focusNextPrevChild(self, nxt: bool) -> bool:
        chain = self._focusables()
        if not chain:
            return True
        cur = QApplication.focusWidget()
        i = chain.index(cur) if cur in chain else (-1 if nxt else 0)
        chain[(i + (1 if nxt else -1)) % len(chain)].setFocus(
            Qt.FocusReason.TabFocusReason if nxt
            else Qt.FocusReason.BacktabFocusReason)
        return True


# ---- confirm ----------------------------------------------------------------
_DANGER_WORDS = ("delete", "remove", "trash", "discard", "erase", "unsubscribe")
_GLYPH_ICONS = {"▲": "alert", "✉": "mail", "⚑": "tag"}


def _is_danger(payload: dict) -> bool:
    if payload.get("danger") is not None:
        return bool(payload["danger"])
    words = f"{payload.get('confirm_label', '')} {payload.get('title', '')}".lower()
    return any(w in words for w in _DANGER_WORDS)


class ConfirmOverlay(_Overlay):
    """The write-confirmation gate. A payload may carry a `confirm_id`, in
    which case a daemon connection is blocked awaiting the answer — so every
    path out, Esc included, answers it. Payload keys: icon, title, intro,
    rows [(key, value)], confirm_label, check_label/check_default, danger,
    toast. A confirm arriving while one is open waits its turn."""

    CARD_W = 500

    def __init__(self, parent):
        super().__init__(parent, dismissable=False)
        self._callback = None
        self._payload: dict = {}
        self._queue: list = []
        self._active = False
        self.check = None
        self.lay = vbox(self.card, (0, 0, 0, 0), 0)

    def open(self, payload: dict, callback):
        if self._active:
            self._queue.append((payload, callback))
            return
        self._active = True
        self._payload, self._callback = payload, callback
        danger = _is_danger(payload)
        clear_layout(self.lay)

        head = hbox(m=(T.S6, T.S6, T.S6, T.S4), s=T.S4)
        name = payload.get("icon", "")
        name = name if name in icons.ICONS else _GLYPH_ICONS.get(name, "alert")
        head.addWidget(IconLabel("trash" if danger else name,
                                 "danger" if danger else "accent", 24), 0,
                       Qt.AlignmentFlag.AlignTop)
        col = vbox(s=T.S2)
        col.addWidget(Heading(payload.get("title", "Confirm"), 3))
        col.addWidget(Badge("warn", "Write action · touches a connected account"),
                      0, Qt.AlignmentFlag.AlignLeft)
        head.addLayout(col, 1)
        self.lay.addLayout(head)

        body = vbox(m=(T.S6, 0, T.S6, T.S5), s=T.S4)
        intro = payload.get("intro", "")
        if intro:
            body.addWidget(Label(intro, "small", wrap=True))
        rows = payload.get("rows") or []
        if rows:
            panel = Panel(padding=T.S4, spacing=T.S2)
            for k, v in rows:
                r = hbox(s=T.S4)
                kl = Label(str(k), "caption")
                kl.setFixedWidth(88)
                kl.setAlignment(Qt.AlignmentFlag.AlignRight
                                | Qt.AlignmentFlag.AlignTop)
                r.addWidget(kl, 0, Qt.AlignmentFlag.AlignTop)
                r.addWidget(Label(str(v), "body", wrap=True, selectable=True), 1)
                panel.lay.addLayout(r)
            body.addWidget(panel)
        # Optional checkbox (rule-create backfill) rides back with the answer.
        self.check = None
        if payload.get("check_label"):
            self.check = QCheckBox(payload["check_label"])
            self.check.setChecked(bool(payload.get("check_default")))
            body.addWidget(self.check)
        self.lay.addLayout(body, 1)

        foot, fl = _dialog_foot()
        fl.addStretch(1)
        cancel = Button("Cancel", "secondary", on_click=lambda: self._answer(False))
        fl.addWidget(cancel)
        ok = Button(payload.get("confirm_label", "Confirm"),
                    "danger" if danger else "primary",
                    on_click=lambda: self._answer(True))
        fl.addWidget(ok)
        self.lay.addWidget(foot)

        self.pop()
        # A destructive write never sits one Enter away.
        (cancel if danger else ok).setFocus()

    def cancel(self):
        self._answer(False)

    def _answer(self, approved: bool):
        if not self._active:
            return          # double click / Esc racing a button
        self._active = False
        self.close_overlay()
        payload = dict(self._payload)
        if self.check is not None:
            payload["check_state"] = self.check.isChecked()
        cb, self._callback = self._callback, None
        if cb:
            cb(approved, payload)
        if self._queue and not self._active:
            nxt = self._queue.pop(0)
            QTimer.singleShot(0, lambda: self.open(*nxt))


# ---- compose ----------------------------------------------------------------
class ComposeOverlay(_Overlay):
    """Draft composer with an inline "ask Lumen to revise" line. A chat-driven
    compose (compose_id) has a blocked daemon connection behind it: Discard/Esc
    answer it with a cancel, Send with the fields."""

    CARD_W = 620

    def __init__(self, parent, state):
        # Not dismissed by an outside click — a stray click shouldn't eat a draft.
        super().__init__(parent, dismissable=False)
        self.state = state
        self._compose_id = None
        self._reply_to = None
        v = vbox(self.card, (0, 0, 0, 0), 0)

        head, self.heading = _dialog_head(
            "New email", "Sent through Gmail — you confirm before it goes",
            close=self.close_it)
        v.addLayout(head)

        body = vbox(m=(T.S6, 0, T.S6, T.S4), s=T.S4)
        self.to_field = TextField("To", "Separate addresses with commas",
                                  "name@example.com")
        self.subject_field = TextField("Subject")
        self.body_field = TextArea("Message", placeholder="Write your message…",
                                   min_height=120)
        self.to = self.to_field.input
        self.subject = self.subject_field.input
        self.body_edit = self.body_field.input
        body.addWidget(self.to_field)
        body.addWidget(self.subject_field)
        body.addWidget(self.body_field, 1)

        rev = Panel(padding=T.S4, spacing=T.S2)
        rl = Label("Ask Lumen to revise", "field-label")
        rev.lay.addWidget(rl)
        row = hbox(s=T.S2)
        self.ask = QLineEdit()
        self.ask.setPlaceholderText("“Make it more formal”, “suggest a subject line”…")
        self.ask.setAccessibleName("Ask Lumen to revise")
        rl.setBuddy(self.ask)
        self.ask.returnPressed.connect(self._revise)
        row.addWidget(self.ask, 1)
        self.revise_btn = Button("Revise", "secondary", icon="edit",
                                 on_click=self._revise)
        row.addWidget(self.revise_btn)
        rev.lay.addLayout(row)
        self.hint = TypingDots("Revising", "small")
        self.hint.setWordWrap(True)
        self.hint.hide()
        rev.lay.addWidget(self.hint)
        body.addWidget(rev)
        v.addLayout(body, 1)

        foot, fl = _dialog_foot()
        fl.addWidget(Button("Discard", "ghost", on_click=self.close_it))
        fl.addStretch(1)
        fl.addWidget(Kbd("Ctrl+Enter"))
        self.send_btn = Button("Review & send", "primary", icon="send",
                               on_click=self._send)
        fl.addWidget(self.send_btn)
        v.addWidget(foot)

        for seq in ("Ctrl+Return", "Ctrl+Enter"):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(self._send)

    def open(self, payload: dict):
        self._compose_id = payload.get("compose_id")
        self._reply_to = payload.get("reply_to")
        # `to` is a list from the daemon's compose_request and a string from
        # the mail screen; setText only takes str (ui_v3 todo-fixes #26).
        to = payload.get("to") or ""
        if isinstance(to, list):
            to = ", ".join(to)
        self.heading.setText("Reply" if self._reply_to else "New email")
        self.to_field.clear_error()
        self.to.setText(to)
        self.subject.setText(payload.get("subject", ""))
        self.body_edit.setPlainText(payload.get("body", ""))
        self.hint.hide()
        self.ask.clear()
        self.revise_btn.set_busy(False)
        self.pop()
        # A reply arrives addressed — land in the body.
        (self.body_edit if to else self.to).setFocus()

    def fields(self) -> dict:
        # The daemon iterates `to`, so hand it a list, never a bare string.
        to = [a.strip() for a in self.to.text().split(",") if a.strip()]
        return {"to": to, "cc": [], "bcc": [],
                "subject": self.subject.text().strip(),
                "body": self.body_edit.toPlainText(),
                "reply_to": self._reply_to}

    def cancel(self):
        self.close_it()

    def close_it(self):
        self.close_overlay()
        if self._compose_id is not None:
            self.state.respond_compose(self._compose_id, None)
            self._compose_id = None

    def _send(self):
        if not self.isVisible():
            return
        fields = self.fields()
        if not fields["to"]:
            self.to_field.set_error("Add at least one recipient.")
            self.to.setFocus()
            return
        self.close_overlay()
        if self._compose_id is not None:
            self.state.respond_compose(self._compose_id, fields)
            self._compose_id = None
            return
        self.state.send_email(fields, self._sent)

    def _sent(self, result):
        r = result or {}
        msg = r.get("message", "Sent")
        self.state.toast_requested.emit(("✓ " if r.get("ok") else "") + msg)

    def _revise(self):
        instruction = self.ask.text().strip()
        if not instruction:
            return
        self.ask.clear()
        self.hint.show()
        self.hint.start("Revising")
        self.revise_btn.set_busy(True, "Revising…")

        def done(result):
            if sip.isdeleted(self):
                return
            self.revise_btn.set_busy(False)
            r = result or {}
            if not r.get("ok"):
                self.hint.set_static(r.get("message", "Revision failed."), "error")
                return
            if r.get("body"):
                self.body_edit.setPlainText(r["body"])
            if r.get("subject"):
                self.subject.setText(r["subject"])
            self.hint.set_static(r.get("note", "Updated the draft."), "small")

        payload = dict(self.fields())
        payload["instruction"] = instruction
        self.state.revise_email(payload, done)


# ---- event ------------------------------------------------------------------
class EventOverlay(_Overlay):
    """New-event composer. Times stay free text; the daemon validates and the
    confirm overlay still gates the actual write."""

    CARD_W = 540

    def __init__(self, parent, state):
        super().__init__(parent)
        self.state = state
        v = vbox(self.card, (0, 0, 0, 0), 0)
        head, _h = _dialog_head(
            "New event", "Google Calendar — you confirm before it's created",
            close=self.close_overlay)
        v.addLayout(head)

        body = vbox(m=(T.S6, 0, T.S6, T.S5), s=T.S4)
        self.title_field = TextField("Title", placeholder="Event title")
        self.title = self.title_field.input
        body.addWidget(self.title_field)
        row = hbox(s=T.S3)
        self.date_field = TextField("Date", placeholder="YYYY-MM-DD")
        self.start_field = TextField("Start", placeholder="14:00")
        self.end_field = TextField("End", placeholder="15:00")
        row.addWidget(self.date_field, 12)
        row.addWidget(self.start_field, 8)
        row.addWidget(self.end_field, 8)
        body.addLayout(row)
        self.date = self.date_field.input
        self.start = self.start_field.input
        self.end = self.end_field.input
        self.location_field = TextField("Location", "Optional")
        self.location = self.location_field.input
        body.addWidget(self.location_field)
        v.addLayout(body, 1)

        foot, fl = _dialog_foot()
        fl.addStretch(1)
        fl.addWidget(Button("Cancel", "secondary", on_click=self.close_overlay))
        fl.addWidget(Button("Review & create", "primary", icon="calendar",
                            on_click=self._create))
        v.addWidget(foot)
        for f in (self.title, self.date, self.start, self.end, self.location):
            f.returnPressed.connect(self._create)

    def open(self, payload: dict):
        for f in (self.title_field, self.date_field):
            f.clear_error()
        self.title.setText(payload.get("title", ""))
        self.date.setText(payload.get("date", ""))
        self.start.setText(payload.get("start", ""))
        self.end.setText(payload.get("end", ""))
        self.location.setText(payload.get("location", "") or "")
        self.pop()
        self.title.setFocus()

    def _create(self):
        title = self.title.text().strip()
        day = self.date.text().strip()
        if not title:
            self.title_field.set_error("Give the event a title.")
            self.title.setFocus()
            return
        if not day:
            self.date_field.set_error("Add a date.")
            self.date.setFocus()
            return
        self.close_overlay()
        self.state.create_event({
            "title": title,
            "start": f"{day}T{self.start.text().strip()}",
            "end": f"{day}T{self.end.text().strip()}",
            "location": self.location.text().strip() or None})


# ---- mail rule --------------------------------------------------------------
def _split(text: str) -> list[str]:
    return [t.strip() for t in text.split(",") if t.strip()]


class RuleDialog(_Overlay):
    """Mail rule: a label plus any-of matchers. Save hands the rule to
    rules.create/update — creation still passes the daemon's confirm gate
    (carrying the apply-to-existing checkbox), so this never writes to Gmail
    itself."""

    CARD_W = 540

    def __init__(self, parent, state):
        super().__init__(parent)
        self.state = state
        self._rid = None
        self.card_lay = vbox(self.card, (0, 0, 0, 0), 0)

    def open(self, prefill: dict | None = None):
        p = prefill or {}
        self._rid = p.get("id")
        self._build(p)
        self.pop()
        self.label_field.input.setFocus()

    def _build(self, p: dict):
        clear_layout(self.card_lay)
        head, _h = _dialog_head(
            "Edit mail rule" if self._rid else "New mail rule",
            "Applied on every sync", close=self.close_overlay)
        self.card_lay.addLayout(head)

        sa = ScrollArea(m=(T.S6, 0, T.S6, T.S4), s=T.S4)
        body = sa.lay
        body.addWidget(Label(
            "Matching mail gets the label and leaves the inbox. Any one "
            "condition is enough.", "small", wrap=True))
        self.label_field = TextField("Label", placeholder="e.g. Bills")
        self.label_field.set_text(p.get("label", ""))
        body.addWidget(self.label_field)
        if self.state.mail_labels:
            host = QWidget()
            chips = FlowLayout(host)
            for name in self.state.mail_labels:
                chips.addWidget(TagChip(
                    name, on_click=lambda n=name: self._pick_label(n)))
            body.addWidget(host)

        def field(caption, key, placeholder, helper=""):
            f = TextField(caption, helper, placeholder)
            f.set_text(", ".join(p.get(key, [])))
            body.addWidget(f)
            return f

        self.from_field = field("From", "from_addrs", "sender@example.com",
                                "Comma-separated addresses")
        self.domain_field = field("Domains", "domains", "example.com")
        self.subj_field = field("Subject keywords", "subject_kw",
                                "invoice, statement")
        self.body_field = field("Body keywords", "body_kw", "")
        self.error = Label("", "error", wrap=True)
        self.error.hide()
        body.addWidget(self.error)
        body.addStretch(1)
        self.card_lay.addWidget(sa, 1)

        foot, fl = _dialog_foot()
        if self._rid is not None:
            fl.addWidget(Button("Delete rule", "danger", icon="trash", size="sm",
                                on_click=self._delete))
        fl.addStretch(1)
        fl.addWidget(Button("Cancel", "secondary", on_click=self.close_overlay))
        fl.addWidget(Button("Save rule", "primary", on_click=self._save))
        self.card_lay.addWidget(foot)

    def _pick_label(self, name: str):
        self.label_field.set_text(name)
        self.label_field.clear_error()

    def _toast(self, fallback: str):
        def done(r):
            r = r or {}
            self.state.toast_requested.emit(
                ("✓ " if r.get("ok") else "") + (r.get("message") or fallback))
            # A save may have backfill-labeled mail — reload rows and chips.
            self.state.refresh_mails()
        return done

    def _save(self):
        rule = {"label": self.label_field.text().strip(),
                "from_addrs": _split(self.from_field.text()),
                "domains": _split(self.domain_field.text()),
                "subject_kw": _split(self.subj_field.text()),
                "body_kw": _split(self.body_field.text())}
        if not rule["label"]:
            self.label_field.set_error("Needs a label.")
            self.label_field.input.setFocus()
            return
        if not any(rule[c] for c in ("from_addrs", "domains", "subject_kw",
                                     "body_kw")):
            self.error.setText("Add at least one condition.")
            self.error.show()
            self.from_field.input.setFocus()
            return
        if self._rid is None:
            self.state.create_rule(rule, self._toast("Saved."))
        else:
            self.state.update_rule(self._rid, rule, self._toast("Saved."))
        self.close_overlay()

    def _delete(self):
        self.state.delete_rule(self._rid, self._toast("Rule deleted."))
        self.close_overlay()


# ---- suggested-label review -------------------------------------------------
class SuggestReviewOverlay(_Overlay):
    """Two-pane review after "suggest labels": every message with a suggested
    label on the left (accept / skip), the full body on the right so the label
    is judged after reading. Skip is local; accept files the mail (the same
    emails.apply_label write a manual label uses). Closes when all are done."""

    CARD_W, CARD_H = 900, 620
    LIST_W = 340

    def __init__(self, parent, state):
        super().__init__(parent)
        self.state = state
        self._sel: str | None = None
        self._shown: list[str] = []
        self._open = False
        self.body = None
        outer = vbox(self.card, (0, 0, 0, 0), 0)

        head = hbox(m=(T.S6, T.S5, T.S5, T.S4), s=T.S3)
        head.addWidget(Heading("Review suggested labels", 3, wrap=False))
        self.count = Badge("accent", "")
        head.addWidget(self.count, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        self.accept_all = Button("Accept all", "secondary", icon="check",
                                 size="sm", on_click=self._accept_all)
        head.addWidget(self.accept_all)
        head.addWidget(Button("Dismiss all", "ghost", size="sm",
                              on_click=self._dismiss))
        head.addWidget(IconButton("x", "Close and dismiss the rest",
                                  on_click=self._dismiss))
        outer.addLayout(head)
        outer.addWidget(Divider())

        panes = hbox(s=0)
        left = ScrollArea(m=(T.S3, T.S3, T.S3, T.S3), s=T.S1)
        left.setFixedWidth(self.LIST_W)
        self.list_lay = left.lay
        panes.addWidget(left)
        panes.addWidget(Divider(vertical=True))
        right = ScrollArea(m=(T.S6, T.S5, T.S6, T.S5), s=0)
        self.pane_lay = right.lay
        panes.addWidget(right, 1)
        outer.addLayout(panes, 1)

        self.state.mails_changed.connect(self._on_mails_changed)

    # ---- open / close -----------------------------------------------------
    def open(self):
        self._sel = None
        self._shown = []
        self._open = True
        self._rebuild()
        if self._open:
            self.pop()

    def _close(self):
        self._open = False
        self.close_overlay()

    def cancel(self):
        self._dismiss()

    def _dismiss(self):
        self._close()
        self.state.dismiss_suggestions()

    def _accept_all(self):
        self._close()
        self.state.accept_all_suggestions()

    # ---- list -------------------------------------------------------------
    def _pending(self) -> list[str]:
        # Cluster same-label suggestions, then by subject — steadier to scan.
        sug = self.state.mail_suggestions
        meta = self.state.mail_suggestion_meta
        return sorted(sug, key=lambda mid: (
            sug[mid].lower(), (meta.get(mid, {}).get("subj") or "").lower()))

    def _on_mails_changed(self):
        # Fires for many reasons; only rebuild when the pending set changed so
        # reading a message never reshuffles the list under the user.
        if self._open and self._pending() != self._shown:
            self._rebuild()

    def _rebuild(self):
        pending = self._pending()
        self._shown = pending
        if not pending:
            self._close()
            return
        if self._sel not in pending:
            self._sel = pending[0]
        n = len(pending)
        self.count.setText(f"{n} to review")
        self.accept_all.setEnabled(n > 0)
        self._render_list()
        self._build_pane()

    def _render_list(self):
        clear_layout(self.list_lay)
        for mid in self._shown:
            self.list_lay.addWidget(self._row(mid))
        self.list_lay.addStretch(1)

    def _row(self, mid: str) -> QWidget:
        meta = self.state.mail_suggestion_meta.get(mid, {})
        name = self.state.mail_suggestions.get(mid, "")
        sender = meta.get("from") or "(unknown)"
        row = ClickRow(on_click=lambda i=mid: self._select(i),
                       selected=(mid == self._sel),
                       accessible_name=f"{sender}: {meta.get('subj', '')}")
        rl = hbox(row, (T.S3, T.S2, T.S2, T.S2), T.S3)
        rl.addWidget(Avatar(Avatar.initials_of(sender), 32), 0,
                     Qt.AlignmentFlag.AlignTop)
        col = vbox(s=2)
        col.addWidget(ElideLabel(sender, "body"))
        col.addWidget(ElideLabel(meta.get("subj") or "(no subject)", "muted"))
        chips = hbox(m=(0, T.S1, 0, 0), s=0)
        chips.addWidget(TagChip(name))
        chips.addStretch(1)
        col.addLayout(chips)
        rl.addLayout(col, 1)
        rl.addWidget(IconButton("check", f"File under {name}", size=32,
                                color="success",
                                on_click=lambda i=mid: self._accept(i)))
        rl.addWidget(IconButton("x", "Skip this one", size=32, color="muted",
                                on_click=lambda i=mid: self._reject(i)))
        return row

    def _select(self, mid: str):
        self._sel = mid
        self._render_list()
        self._build_pane()

    # ---- reading pane -----------------------------------------------------
    def _build_pane(self):
        clear_layout(self.pane_lay)
        mid = self._sel
        if mid is None:
            self.pane_lay.addWidget(EmptyState(
                "check", "All caught up", "Every suggestion has been handled."))
            return
        meta = self.state.mail_suggestion_meta.get(mid, {})
        name = self.state.mail_suggestions.get(mid, "")
        sender = meta.get("from", "")

        self.pane_lay.addWidget(Heading(meta.get("subj") or "(no subject)", 3))
        self.pane_lay.addSpacing(T.S3)
        who = hbox(m=(0, 0, 0, T.S4), s=T.S3)
        who.addWidget(Avatar(Avatar.initials_of(sender), 36))
        wc = vbox(s=2)
        wc.addWidget(Label(sender, "body"))
        wc.addWidget(Label(f"to me · {meta.get('date', '')}", "meta"))
        who.addLayout(wc, 1)
        self.pane_lay.addLayout(who)

        dec = Panel(padding=T.S3, spacing=T.S3)
        dec.lay.setDirection(dec.lay.Direction.LeftToRight)
        dec.lay.addWidget(Label("Suggested", "caption"))
        dec.lay.addWidget(TagChip(name))
        dec.lay.addStretch(1)
        dec.lay.addWidget(Button(f"File under {name}", "primary", icon="check",
                                 size="sm", on_click=lambda: self._accept(mid)))
        dec.lay.addWidget(Button("Skip", "secondary", size="sm",
                                 on_click=lambda: self._reject(mid)))
        self.pane_lay.addWidget(dec)
        self.pane_lay.addSpacing(T.S4)

        self.body = MailBody(self.state)
        self.pane_lay.addWidget(self.body, 1)
        self.pane_lay.addStretch(1)
        # Meta rows carry no body_html (kept light); pull the full message now.
        self.state.fetch_mail(mid, lambda row, i=mid: self._body_ready(i, row))

    def _body_ready(self, mid: str, row: dict | None):
        # The user may have moved on before the fetch returned.
        if (self._sel != mid or row is None or self.body is None
                or sip.isdeleted(self.body)):
            return
        self.body.show_mail(row)

    # ---- actions ----------------------------------------------------------
    def _accept(self, mid: str):
        self.state.apply_suggestion(mid)
        # apply_suggestion pops the suggestion now; mails_changed lands later.
        if self._open:
            self._rebuild()

    def _reject(self, mid: str):
        self.state.reject_suggestion(mid)
        if self._open:
            self._rebuild()


# ---- command palette --------------------------------------------------------
class CommandPalette(_Overlay):
    """Ctrl+K: jump to any screen or run a common action. Type to filter,
    arrows to move, Enter to run, Esc to close. Anything that matches nothing
    can be asked of Lumen instead."""

    CARD_W = 580
    CARD_NAME = "palette"

    def __init__(self, window):
        super().__init__(window, top=True)
        self.win = window
        self._entries: list[tuple] = []
        self._shown: list[tuple] = []
        v = vbox(self.card, (0, 0, 0, 0), 0)

        head = hbox(m=(T.S4, T.S3, T.S4, T.S3), s=T.S3)
        head.addWidget(IconLabel("search", "muted"))
        self.input = QLineEdit()
        self.input.setProperty("role", "bare")
        self.input.setPlaceholderText("Jump to a screen or run an action…")
        self.input.setAccessibleName("Command palette")
        self.input.textChanged.connect(self._filter)
        self.input.returnPressed.connect(self._activate)
        self.input.installEventFilter(self)
        head.addWidget(self.input, 1)
        head.addWidget(Kbd("Esc"))
        v.addLayout(head)
        v.addWidget(Divider())

        self.list = QListWidget()
        self.list.setObjectName("paletteList")
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setMaximumHeight(380)
        self.list.itemClicked.connect(lambda _i: self._activate())
        wrap = vbox(m=(T.S2, T.S2, T.S2, T.S2), s=0)
        wrap.addWidget(self.list)
        v.addLayout(wrap, 1)

        foot, fl = _dialog_foot()
        fl.setContentsMargins(T.S4, T.S2, T.S4, T.S2)
        for key, what in (("↑ ↓", "move"), ("Enter", "run"), ("Esc", "close")):
            fl.addWidget(Kbd(key))
            fl.addWidget(Label(what, "caption"))
            fl.addSpacing(T.S2)
        fl.addStretch(1)
        v.addWidget(foot)

    def _build_entries(self) -> list[tuple]:
        """(title, icon, hint, fn, keywords)."""
        w, st = self.win, self.win.state
        out = [(f"Go to {title}", icon, hint,
                (lambda k=key: w.switch_to(k)), key)
               for key, title, icon, hint in w.NAV_ENTRIES]
        out += [
            ("New todo", "plus", "", lambda: w.switch_to("todos", new=True),
             "add task capture"),
            ("Compose email", "edit", "", lambda: st.open_compose({}),
             "write mail send new message"),
            ("New calendar event", "calendar", "", lambda: w.open_event({}),
             "create meeting schedule"),
            ("Ask Lumen", "ask", "Ctrl+8", lambda: w.switch_to("ask", focus=True),
             "chat question"),
            ("Switch theme", "contrast", "", w.cycle_theme,
             "dark light mode appearance"),
            ("Go back", "arrow-left", "Alt+←", w.go_back, "history previous"),
            ("Go forward", "arrow-right", "Alt+→", w.go_forward, "history next"),
        ]
        return out

    def open(self):
        if self.isVisible():
            return
        self._entries = self._build_entries()
        self.input.clear()
        self._filter("")
        self.pop()
        self.input.setFocus()

    def _filter(self, text: str):
        q = text.strip().lower()
        self._shown = [e for e in self._entries
                       if not q or q in e[0].lower() or q in e[4]]
        if q:
            question = text.strip()
            self._shown.append((f"Ask Lumen: “{question}”", "ask", "",
                                lambda qq=question: self.win.ask(
                                    qq, self.win.current_ask_context()), ""))
        self.list.clear()
        for title, icon, hint, _fn, _kw in self._shown:
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, T.ROW_H))
            item.setData(Qt.ItemDataRole.AccessibleTextRole, title)
            self.list.addItem(item)
            row = QWidget()
            h = hbox(row, (T.S3, 0, T.S3, 0), T.S3)
            h.addWidget(IconLabel(icon, "fg2"))
            h.addWidget(Label(title, "body"), 1)
            if hint:
                h.addWidget(Kbd(hint))
            self.list.setItemWidget(item, row)
        if self._shown:
            self.list.setCurrentRow(0)

    def eventFilter(self, obj, ev):
        if obj is self.input and ev.type() == QEvent.Type.KeyPress:
            n = self.list.count()
            if ev.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up) and n:
                step = 1 if ev.key() == Qt.Key.Key_Down else -1
                self.list.setCurrentRow((self.list.currentRow() + step) % n)
                return True
        return super().eventFilter(obj, ev)

    def _activate(self):
        i = self.list.currentRow()
        if not (0 <= i < len(self._shown)):
            return
        fn = self._shown[i][3]
        self.close_overlay()
        fire_on_next_tick(fn)


# ---- toast ------------------------------------------------------------------
_TOAST_ICONS = (("✓", "check"), ("⚠", "alert"), ("✕", "alert-circle"))


class Toast(QFrame):
    """Bottom-center pill. Optional Undo. 220ms in, 150ms out; instant under
    reduced motion. `bottom_inset` keeps it clear of the Ask bar."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("toast")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.bottom_inset = T.S6
        self._undo = None
        h = hbox(self, (T.S5, T.S3, T.S3, T.S3), T.S3)
        self.icon = IconLabel("check", "toast_fg", T.ICON_SM)
        self.icon.hide()
        h.addWidget(self.icon)
        self.text = QLabel()
        self.text.setObjectName("toastText")
        self.text.setWordWrap(True)
        h.addWidget(self.text, 1)
        self.action = QPushButton("Undo")
        self.action.setObjectName("toastAction")
        self.action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action.clicked.connect(self._do_undo)
        self.action.hide()
        h.addWidget(self.action)
        h.addSpacing(T.S2)

        self._fx = QGraphicsOpacityEffect(self)
        self._fx.setOpacity(1.0)
        self.setGraphicsEffect(self._fx)
        self._anim = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self.hide()

    def pop(self, message: str, undo=None):
        if not message:
            return
        icon = None
        for glyph, name in _TOAST_ICONS:
            if message.startswith(glyph):
                icon, message = name, message[len(glyph):].lstrip()
                break
        if icon:
            self.icon.set_icon(icon)
        self.icon.setVisible(bool(icon))
        self.text.setText(message)
        self.setAccessibleName(message)
        self._undo = undo
        self.action.setVisible(undo is not None)
        self.layout().setContentsMargins(T.S5, T.S3, T.S3 if undo else T.S5, T.S3)
        self._timer.setInterval(5000 if undo else 2800)
        self.reposition()
        self._animate_in()
        self._timer.start()

    show_message = pop

    def reposition(self):
        p = self.parentWidget()
        if p is None:
            return
        self.setMaximumWidth(min(560, p.width() - 2 * T.S6))
        self.adjustSize()
        self.move(self._home())

    def _home(self) -> QPoint:
        p = self.parentWidget()
        return QPoint((p.width() - self.width()) // 2,
                      p.height() - self.height() - self.bottom_inset)

    def _stop(self):
        if self._anim is not None:
            self._anim.stop()
            self._anim = None

    def _animate_in(self):
        self._stop()
        home = self._home()
        dur = T.ms(T.TOGGLE_MS)
        self.show()
        self.raise_()
        if not dur:
            self._fx.setOpacity(1.0)
            self.move(home)
            return
        g = QParallelAnimationGroup(self)
        a = QPropertyAnimation(self._fx, b"opacity")
        a.setDuration(dur)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.setEasingCurve(QEasingCurve.Type.OutCubic)
        b = QPropertyAnimation(self, b"pos")
        b.setDuration(dur)
        b.setStartValue(home + QPoint(0, 12))
        b.setEndValue(home)
        b.setEasingCurve(QEasingCurve.Type.OutCubic)
        g.addAnimation(a)
        g.addAnimation(b)
        self._anim = g
        g.start()

    def dismiss(self):
        self._timer.stop()
        if not self.isVisible():
            return
        self._stop()
        dur = T.ms(T.EXIT_MS)
        if not dur:
            self.hide()
            return
        a = QPropertyAnimation(self._fx, b"opacity", self)
        a.setDuration(dur)
        a.setStartValue(self._fx.opacity())
        a.setEndValue(0.0)
        a.setEasingCurve(QEasingCurve.Type.InCubic)
        a.finished.connect(self._finish_out)
        self._anim = a
        a.start()

    def _finish_out(self):
        self._anim = None
        self.hide()
        self._fx.setOpacity(1.0)

    def _do_undo(self):
        fn, self._undo = self._undo, None
        self.dismiss()
        if fn is not None:
            fire_on_next_tick(fn)
