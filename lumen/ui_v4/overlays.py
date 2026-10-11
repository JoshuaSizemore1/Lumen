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
import re
import time
from datetime import date, timedelta

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

from lumen.daemon.llm.email_compose import split_addresses

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
    toast. A confirm arriving while one is open waits its turn.

    It can arrive mid-keystroke or under a moving mouse (todo-fixes #80): a
    Space typed into the Ask bar used to press the focused Approve. So focus
    goes to the card, not a button, and Approve ignores clicks for ARM_MS
    after the card — or the window, re-shown from the tray — appears."""

    CARD_W = 500
    ARM_MS = 500

    def __init__(self, parent):
        super().__init__(parent, dismissable=False)
        self._callback = None
        self._payload: dict = {}
        self._queue: list = []
        self._active = False
        self._armed_at = 0.0
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
                    on_click=self._approve_clicked)
        fl.addWidget(ok)
        self.lay.addWidget(foot)

        self.pop()
        # No write sits one Space or Enter away: Tab reaches Cancel first.
        self.card.setFocus(Qt.FocusReason.OtherFocusReason)
        self._armed_at = time.monotonic()

    def _approve_clicked(self):
        if time.monotonic() - self._armed_at < self.ARM_MS / 1000:
            return          # the click was meant for whatever was there before
        self._answer(True)

    def eventFilter(self, obj, ev):
        # Show only, not WindowActivate: activation would silently swallow a
        # deliberate first click on Approve in a visible, unfocused window.
        if (obj is self.parentWidget() and self._active
                and ev.type() == QEvent.Type.Show):
            self._armed_at = time.monotonic()
        return super().eventFilter(obj, ev)

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
        # No success toast here: Approve only starts the action. Each caller
        # says it worked once the daemon answers (L7).
        if self._queue and not self._active:
            nxt = self._queue.pop(0)
            QTimer.singleShot(0, lambda: self.open(*nxt))


# ---- unsaved work on quit (#88) ----------------------------------------------
class UnsavedOverlay(_Overlay):
    """Quitting with unsaved Files edits: Save / Don't save / Cancel. Local
    files only, so no "touches a connected account" badge. Esc cancels, and
    focus goes to the card so a stray Enter decides nothing."""

    CARD_W = 460

    def __init__(self, parent):
        super().__init__(parent, dismissable=False)
        self._on_save = self._on_discard = None
        self.lay = vbox(self.card, (0, 0, 0, 0), 0)

    def open(self, names: list[str], on_save, on_discard):
        self._on_save, self._on_discard = on_save, on_discard
        clear_layout(self.lay)
        head = hbox(m=(T.S6, T.S6, T.S6, T.S4), s=T.S4)
        head.addWidget(IconLabel("save", "accent", 24), 0,
                       Qt.AlignmentFlag.AlignTop)
        head.addWidget(Heading("Save your changes before quitting?", 3,
                               wrap=True), 1)
        self.lay.addLayout(head)
        shown = ", ".join(names[:4]) + (f" and {len(names) - 4} more"
                                        if len(names) > 4 else "")
        body = vbox(m=(T.S6, 0, T.S6, T.S5), s=T.S2)
        body.addWidget(Label(
            f"You have unsaved edits in {shown}. If you don't save them, "
            "they're gone when Lumen quits.", "small", wrap=True))
        self.lay.addLayout(body, 1)
        foot, fl = _dialog_foot()
        fl.addWidget(Button("Don't save", "ghost",
                            on_click=lambda: self._answer(self._on_discard)))
        fl.addStretch(1)
        fl.addWidget(Button("Cancel", "secondary", on_click=self.cancel))
        fl.addWidget(Button("Save and quit", "primary",
                            on_click=lambda: self._answer(self._on_save)))
        self.lay.addWidget(foot)
        self.pop()
        self.card.setFocus(Qt.FocusReason.OtherFocusReason)

    def _answer(self, fn):
        self._on_save = self._on_discard = None
        self.close_overlay()
        if fn is not None:
            fn()


# ---- compose ----------------------------------------------------------------
class ComposeOverlay(_Overlay):
    """Draft composer with an inline "ask Lumen to revise" line. A chat-driven
    compose (compose_id) has a blocked daemon connection behind it: Discard
    answers it with a cancel, Send with the fields. Esc and the X ask first
    when there's something to lose (todo-fixes #106)."""

    CARD_W = 620

    def __init__(self, parent, state):
        # Not dismissed by an outside click — a stray click shouldn't eat a draft.
        super().__init__(parent, dismissable=False)
        self.state = state
        self._compose_id = None
        self._reply_to = None
        self._note = ""
        self._opened = ("", "", "", "")
        v = vbox(self.card, (0, 0, 0, 0), 0)

        head, self.heading = _dialog_head(
            "New email", "Sent through Gmail — you confirm before it goes",
            close=self.cancel)
        v.addLayout(head)

        body = vbox(m=(T.S6, 0, T.S6, T.S4), s=T.S4)
        # A caller's warning about this draft (e.g. attachments a forward
        # can't carry).
        self.note = Panel(padding=T.S3, spacing=0)
        nrow = hbox(s=T.S2)
        nrow.addWidget(IconLabel("paperclip", "warn", T.ICON_SM), 0,
                       Qt.AlignmentFlag.AlignTop)
        self.note_text = Label("", "small", wrap=True)
        nrow.addWidget(self.note_text, 1)
        self.note.lay.addLayout(nrow)
        self.note.hide()
        body.addWidget(self.note)
        self.to_field = TextField("To", "Separate addresses with commas",
                                  "name@example.com")
        # A drafted Cc used to be dropped on Send (todo-fixes #105).
        self.cc_field = TextField("Cc", placeholder="Optional")
        self.subject_field = TextField("Subject")
        self.body_field = TextArea("Message", placeholder="Write your message…",
                                   min_height=120)
        self.to = self.to_field.input
        self.cc = self.cc_field.input
        self.subject = self.subject_field.input
        self.body_edit = self.body_field.input
        body.addWidget(self.to_field)
        body.addWidget(self.cc_field)
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
        self.discard_btn = Button("Discard", "ghost", on_click=self.close_it)
        fl.addWidget(self.discard_btn)
        fl.addStretch(1)
        self.send_kbd = Kbd("Ctrl+Enter")
        fl.addWidget(self.send_kbd, 0, Qt.AlignmentFlag.AlignVCenter)
        # "&&": a lone & is a mnemonic marker and rendered "Review _send" (L1).
        self.send_btn = Button("Review && send", "primary", icon="send",
                               on_click=self._send)
        fl.addWidget(self.send_btn)
        # Esc / X with something to lose ask here instead (#106). Inline, so
        # nothing stacks on the compose card.
        self.ask_discard = Label("Discard this draft?", "small")
        self.keep_btn = Button("Keep editing", "secondary",
                               on_click=self._keep_editing)
        self.drop_btn = Button("Discard draft", "danger", on_click=self.close_it)
        for w in (self.ask_discard, self.keep_btn, self.drop_btn):
            w.hide()
        fl.insertWidget(0, self.ask_discard, 0, Qt.AlignmentFlag.AlignVCenter)
        fl.addWidget(self.keep_btn)
        fl.addWidget(self.drop_btn)
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
        def joined(key):
            v = payload.get(key) or ""
            return ", ".join(v) if isinstance(v, list) else str(v)

        to, cc = joined("to"), joined("cc")
        self.heading.setText("Reply" if self._reply_to else "New email")
        self._note = payload.get("note") or ""
        self.note_text.setText(self._note)
        self.note.setVisible(bool(self._note))
        self.to_field.clear_error()
        self.cc_field.clear_error()
        self.to.setText(to)
        self.cc.setText(cc)
        self.subject.setText(payload.get("subject", ""))
        self.body_edit.setPlainText(payload.get("body", ""))
        self._opened = self._snapshot()
        self.hint.hide()
        self.ask.clear()
        self.revise_btn.set_busy(False)
        self._keep_editing()
        self.pop()
        # A reply arrives addressed — land in the body.
        (self.body_edit if to else self.to).setFocus()

    def fields(self) -> dict:
        # Entries as typed ("Name <addr>", "Last, First" <addr>, `,`/`;`
        # lists); the daemon validates each, and a rejected one comes
        # back to the draft unchanged (#90).
        return {"to": split_addresses(self.to.text()),
                "cc": split_addresses(self.cc.text()), "bcc": [],
                "subject": self.subject.text().strip(),
                "body": self.body_edit.toPlainText(),
                "reply_to": self._reply_to}

    def _snapshot(self) -> tuple:
        return (self.to.text(), self.cc.text(), self.subject.text(),
                self.body_edit.toPlainText())

    def _has_work(self) -> bool:
        """Something an Esc would throw away: a message (typed, or a draft
        Lumen wrote), or any edit since the window opened."""
        return (bool(self.body_edit.toPlainText().strip())
                or self._snapshot() != self._opened)

    def cancel(self):
        """Esc and the X. Asks first when there's something to lose (#106);
        Esc again while it's asking keeps editing."""
        if self.drop_btn.isVisible():
            self._keep_editing()
        elif self._has_work():
            for w in (self.discard_btn, self.send_kbd, self.send_btn):
                w.hide()
            for w in (self.ask_discard, self.keep_btn, self.drop_btn):
                w.show()
            self.keep_btn.setFocus()
        else:
            self.close_it()

    def _keep_editing(self):
        refocus = self.keep_btn.hasFocus() or self.drop_btn.hasFocus()
        for w in (self.ask_discard, self.keep_btn, self.drop_btn):
            w.hide()
        for w in (self.discard_btn, self.send_kbd, self.send_btn):
            w.show()
        if refocus:
            self.body_edit.setFocus()

    def close_it(self):
        self.close_overlay()
        if self._compose_id is not None:
            self.state.respond_compose(self._compose_id, None)
            self._compose_id = None

    def _send(self):
        """Send button and Ctrl+Enter: review first. A send is an outside
        write, and the caption promises a confirm before it goes (Josh,
        2026-10-10, plan L14)."""
        if not self.isVisible():
            return
        fields = self.fields()
        if not fields["to"]:
            self.to_field.set_error("Add at least one recipient.")
            self.to.setFocus()
            return
        body = fields["body"].strip()
        rows = [("To", ", ".join(fields["to"]))]
        if fields["cc"]:
            rows.append(("Cc", ", ".join(fields["cc"])))
        rows.append(("Subject", fields["subject"] or "(no subject)"))
        if body:
            rows.append(("Message", body if len(body) <= 280
                         else body[:279].rstrip() + "…"))
        payload = {"icon": "send", "title": "Send this email?",
                   "intro": "Lumen sends it from your Gmail now.",
                   "rows": rows, "confirm_label": "Send", "danger": False}
        confirm = getattr(self.parentWidget(), "confirm", None)
        if confirm is None:
            self._really_send(fields)
            return
        confirm.open(payload, lambda ok, _p: ok and self._really_send(fields))

    def _really_send(self, fields: dict):
        if not self.isVisible():
            return
        self.close_overlay()
        note = self._note

        def _reopen(msg):
            # Draft would otherwise be gone — restore it with the error so
            # the user can correct the address or retry.  The caller's note
            # (a forward's "attachments aren't included") travels with it.
            self.state.toast_requested.emit(msg)
            self.open({"to": fields["to"], "cc": fields["cc"],
                       "subject": fields["subject"],
                       "body": fields["body"], "reply_to": fields.get("reply_to"),
                       "note": note})

        if self._compose_id is not None:
            compose_id = self._compose_id
            self._compose_id = None

            def compose_done(result):
                r = result or {}
                if not r.get("ok"):
                    _reopen(r.get("message")
                            or "Couldn't send. Your draft is back.")
                elif r.get("message"):
                    # The chat had stopped waiting (30 min), so this reply
                    # did the sending and no chat line will say so (#111).
                    self.state.toast_requested.emit("✓ " + r["message"])

            def compose_failed(_msg):
                _reopen("Couldn't send. Your draft is back.")

            self.state.respond_compose(compose_id, fields, compose_done,
                                       on_error=compose_failed)
            return

        def done(result):
            r = result or {}
            if r.get("ok"):
                self.state.toast_requested.emit("✓ " + r.get("message", "Sent"))
            else:
                _reopen(r.get("message") or "Couldn't send. Your draft is back.")

        def failed(_msg):
            _reopen("Couldn't send. Your draft is back.")

        self.state.send_email(fields, done, on_error=failed)

    def _revise(self):
        instruction = self.ask.text().strip()
        if not instruction:
            return
        self.ask.clear()
        self.hint.show()
        self.hint.set_role("small")       # a previous failure left it red
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

        def failed(msg):
            if sip.isdeleted(self):
                return
            self.revise_btn.set_busy(False)
            self.hint.set_static(f"Revision failed: {msg}", "error")

        payload = dict(self.fields())
        payload["instruction"] = instruction
        self.state.revise_email(payload, done, on_error=failed)


# ---- event ------------------------------------------------------------------
_CLOCK = re.compile(r"^(\d{1,2})(?::?(\d{2}))?\s*(?:([ap])\.?\s*m?\.?)?$", re.I)


def parse_clock(text: str, end: bool = False) -> int | None:
    """Minutes after midnight for "9:30", "09:30", "930", "14", "2pm" or
    "2:30 p.m.", or None. `end` also allows "24:00" (midnight at the end of
    the day)."""
    m = _CLOCK.match((text or "").strip())
    if not m:
        return None
    h, mins = int(m.group(1)), int(m.group(2) or 0)
    if mins > 59:
        return None
    if m.group(3):
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if m.group(3).lower() == "p" else 0)
    if end and h == 24 and mins == 0:
        return 24 * 60
    return h * 60 + mins if h <= 23 else None


class EventOverlay(_Overlay):
    """New-event composer. The form checks what it can and stays open until
    the daemon answers, so nothing typed is lost to a rejected time or an
    unreachable Google (todo-fixes #99). The confirm overlay still gates the
    actual write."""

    CARD_W = 540
    BAD_TIME = "Try 14:00, 9:30 or 2pm."

    def __init__(self, parent, state):
        super().__init__(parent)
        self.state = state
        self._req = 0
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
        # Top-aligned: an error under one field mustn't push the others down.
        for field, stretch in ((self.date_field, 12), (self.start_field, 8),
                               (self.end_field, 8)):
            row.addWidget(field, stretch, Qt.AlignmentFlag.AlignTop)
        body.addLayout(row)
        self.date = self.date_field.input
        self.start = self.start_field.input
        self.end = self.end_field.input
        self.all_day = QCheckBox("All day")
        self.all_day.toggled.connect(self._all_day_toggled)
        body.addWidget(self.all_day)
        self.location_field = TextField("Location", "Optional")
        self.location = self.location_field.input
        body.addWidget(self.location_field)
        v.addLayout(body, 1)

        foot, fl = _dialog_foot()
        self.status = Label("", "small", wrap=True)
        fl.addWidget(self.status, 1)
        fl.addWidget(Button("Cancel", "secondary", on_click=self.close_overlay))
        self.create_btn = Button("Review && create", "primary", icon="calendar",
                                 on_click=self._create)
        fl.addWidget(self.create_btn)
        v.addWidget(foot)
        for f in (self.title, self.date, self.start, self.end, self.location):
            f.returnPressed.connect(self._create)

    def open(self, payload: dict):
        self._req += 1                  # an answer for an older form is dropped
        for f in (self.title_field, self.date_field, self.start_field,
                  self.end_field):
            f.clear_error()
        self.title.setText(payload.get("title", ""))
        self.date.setText(payload.get("date", ""))
        self.start.setText(payload.get("start", ""))
        self.end.setText(payload.get("end", ""))
        self.all_day.setChecked(bool(payload.get("all_day")))
        self._all_day_toggled(self.all_day.isChecked())
        self.location.setText(payload.get("location", "") or "")
        self._set_status("")
        self.create_btn.set_busy(False)
        self.pop()
        self.title.setFocus()

    def _all_day_toggled(self, on: bool):
        self.start_field.setVisible(not on)
        self.end_field.setVisible(not on)

    def _set_status(self, text: str, role: str = "small"):
        self.status.setText(text)
        self.status.set_role(role)

    def _fail(self, field, msg: str):
        field.set_error(msg)
        field.input.setFocus()

    def _proposal(self) -> dict | None:
        """What the daemon gets, or None after flagging the field to fix."""
        title = self.title.text().strip()
        if not title:
            return self._fail(self.title_field, "Give the event a title.")
        try:
            day = date.fromisoformat(self.date.text().strip())
        except ValueError:
            return self._fail(self.date_field, "Use a date like "
                              f"{date.today().isoformat()}.")
        out = {"title": title, "location": self.location.text().strip() or None}
        if self.all_day.isChecked():
            return {**out, "all_day": True, "start": day.isoformat(),
                    "end": day.isoformat()}
        s = parse_clock(self.start.text())
        if s is None:
            return self._fail(self.start_field, self.BAD_TIME)
        out["start"] = f"{day.isoformat()}T{s // 60:02d}:{s % 60:02d}"
        if self.end.text().strip():
            e = parse_clock(self.end.text(), end=True)
            if e is None:
                return self._fail(self.end_field, self.BAD_TIME)
            # An end at or before the start runs past midnight, as in the
            # edit dialog (L5).
            end_day = day + timedelta(days=1) if e <= s else day
            if e == 24 * 60:
                e, end_day = 0, day + timedelta(days=1)
            out["end"] = f"{end_day.isoformat()}T{e // 60:02d}:{e % 60:02d}"
        # A blank End takes the daemon's default length.
        return out

    def _create(self):
        if not self.isVisible() or not self.create_btn.isEnabled():
            return                      # already waiting on the daemon
        proposal = self._proposal()
        if proposal is None:
            return
        if not getattr(self.state, "live", True):
            self.close_overlay()        # sample mode: the demo confirm shows
            self.state.create_event(proposal)
            return
        self._req += 1
        mine = self._req
        self._set_status("")
        self.create_btn.set_busy(True, "Waiting…")

        def done(result):
            if mine != self._req or sip.isdeleted(self):
                return
            self.create_btn.set_busy(False)
            r = result or {}
            if r.get("created"):
                self.close_overlay()
                self.state.toast_requested.emit(
                    f"✓ Added “{proposal['title']}” to your calendar.")
                self._show_new_event()
            elif r.get("cancelled"):
                self._set_status("")     # back to the form, as typed
            else:
                self._set_status(r.get("message")
                                 or "Couldn't create the event.", "error")

        def failed(msg):
            if mine != self._req or sip.isdeleted(self):
                return
            self.create_btn.set_busy(False)
            self._set_status(msg or "Couldn't create the event.", "error")

        self.state.create_event(proposal, done, on_error=failed)

    def _show_new_event(self):
        """Today and Calendar re-read the daemon's cache, which it synced
        right after creating."""
        win = self.parentWidget()
        for key in ("today", "calendar"):
            screen = win.screen(key) if hasattr(win, "screen") else None
            if screen is not None and hasattr(screen, "refresh"):
                screen.refresh()


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
            sig = getattr(self.state, "rules_changed", None)
            if sig is not None:
                sig.emit()
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
    emails.apply_label write a manual label uses). Closes when all are done.

    Esc, an outside click and the X only close it: the rest stay waiting
    behind the Inbox's "N to review" banner. Only "Dismiss all" drops them —
    they can be a 40-message model run (todo-fixes #107)."""

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
        head.addWidget(IconButton("x", "Close — the rest wait in Inbox",
                                  on_click=self.cancel))
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
        self._close()

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
            ("Morning briefing", "sun", "", lambda: w.switch_to("today", brief=True),
             "today summary day overview"),
            ("Ask about this page", "ask", "Ctrl+L", w._focus_ask,
             "question ask bar"),
            ("Ask Lumen", "ask", "Ctrl+8", lambda: w.switch_to("ask", focus=True),
             "chat question conversation"),
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
            bar = getattr(self.win, "askbar", None)
            if bar is not None and bar.isVisible():
                # On a page with an Ask bar, answer there, like the bar does.
                self._shown.append((
                    f"Ask about this page: “{question}”", "ask", "",
                    lambda qq=question: bar.ask(
                        qq, self.win.current_ask_context()), ""))
            else:
                self._shown.append((
                    f"Ask Lumen: “{question}”", "ask", "",
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
