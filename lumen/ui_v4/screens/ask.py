"""Ask Lumen: the one conversation. Replaces ui_v3's Chat screen, and is where
the Ask bar's questions land (`window.ask` -> `start(question, context)`).

Streams exactly like ui_v3 chat.py: `window.chat_client.send("chat", {...})`,
with chunk / done / error / tool_used / cold_start / model_off / conversation /
via / claude_unavailable handled the same way. The thread id is app-wide
(`state.active_conv_id`), so every surface continues the same conversation.

Conversation list: `state.list_conversations`, `get_conversation`,
`delete_conversation`, plus "New conversation" (clears active_conv_id).
There is no rename: ui_v3 has none and the daemon has no rename route.

Differences from ui_v3 chat, all additive:
  - A question from the Ask bar carries the page it came from (the context
    goes to the daemon as `context`, and the thread shows "Asked from X").
    Ask-bar questions keep ui_v3's `capture_ok`, so a note-shaped one becomes
    a todo; the `captured` event is shown instead of an empty reply.
  - The composer is multi-line: Enter sends, Shift+Enter adds a line.
  - Below ~760px the conversation list folds behind a "Conversations" button.
"""
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QTextOption
from PyQt6.QtWidgets import QFrame, QPlainTextEdit, QSizePolicy, QWidget

from .. import theme as T
from ..components import (
    NO_REPLY_STATUS, NO_REPLY_TEXT, Badge, Button, ClaudeUnavailableNotice,
    ClickRow, Divider, ElideLabel, EmptyState, Eyebrow, IconButton, IconLabel,
    Kbd, Label, ModelOffNotice, ScreenHeader, ScrollArea, SkeletonRow,
    TypingDots, clear_layout, hbox, vbox,
)

WAKE_THRESHOLD_MS = 1500
LIST_W = 272
NARROW = 760           # screen width below which the list folds away
BUBBLE_MAX_W = 680
_SCREEN_NAMES = {"today": "Today", "mail": "Inbox", "calendar": "Calendar",
                 "todos": "Todos", "books": "Books", "files": "Files",
                 "canvas": "Canvas", "settings": "Settings"}


def _context_dict(context, from_key: str | None) -> dict:
    """The daemon reads `context` as a dict ({screen, file/dir, ...}). Screens
    may hand back a dict (ui_v3 style) or a plain-text summary."""
    if isinstance(context, dict):
        ctx = dict(context)
        if from_key and not ctx.get("screen"):
            ctx["screen"] = from_key
        return ctx
    ctx = {"screen": from_key} if from_key else {}
    if isinstance(context, str) and context.strip():
        ctx["summary"] = context.strip()[:8000]
    return ctx


def _when(stamp) -> str:
    """'14:05' today, 'Sep 3' otherwise; the raw value if it won't parse."""
    from datetime import datetime
    raw = str(stamp or "").strip()
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw[:16]
    if dt.tzinfo is not None:
        dt = dt.astimezone()
    if dt.date() == datetime.now().date():
        return dt.strftime("%H:%M")
    return f"{dt.strftime('%b')} {dt.day}"


class _Composer(QPlainTextEdit):
    """Enter sends; Shift+Enter inserts a newline. Grows with its text up to
    a cap, then scrolls."""

    MIN_H, MAX_H = 44, 168

    def __init__(self, on_send):
        super().__init__()
        self._on_send = on_send
        self.setPlaceholderText("Ask Lumen anything…")
        self.setAccessibleName("Message Lumen")
        self.setTabChangesFocus(True)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(self.MIN_H)
        self.document().documentLayout().documentSizeChanged.connect(
            lambda _s: self._fit())

    def _fit(self):
        doc_h = self.document().size().height() * self.fontMetrics().lineSpacing()
        h = int(max(self.MIN_H, min(self.MAX_H, doc_h + 22)))
        if h != self.height():
            self.setFixedHeight(h)

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.insertPlainText("\n")
            else:
                self._on_send()
            return
        super().keyPressEvent(ev)


class _Bubble(QFrame):
    """One message. The user's sit right on the sunken tone; Lumen's sit left
    on the surface card. `body` is the text label (updated while streaming)."""

    def __init__(self, role: str, text: str = "", note: str = ""):
        super().__init__()
        is_user = role == "user"
        self.setProperty("role", "panel" if is_user else "card")
        self.setMaximumWidth(BUBBLE_MAX_W)
        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Preferred)
        v = vbox(self, (T.S4, T.S3, T.S4, T.S3), T.S2)
        head = hbox(s=T.S2)
        self.who = Eyebrow("You" if is_user else "Lumen")
        head.addWidget(self.who)
        head.addStretch(1)
        v.addLayout(head)
        self.tool = Label("", "muted", wrap=True)
        self.tool.hide()
        v.addWidget(self.tool)
        self.status = TypingDots("Thinking", "muted")
        self.status.setText("")
        self.status.hide()
        v.addWidget(self.status)
        self.body = Label(text, "body", wrap=True, selectable=True)
        self.body.setTextFormat(Qt.TextFormat.PlainText)
        self.body.setVisible(bool(text))
        v.addWidget(self.body)
        self.foot = Label(note, "muted", wrap=True)
        self.foot.setVisible(bool(note))
        v.addWidget(self.foot)

    def set_text(self, text: str) -> None:
        self.body.setText(text)
        self.body.setVisible(bool(text))

    def set_status(self, text: str) -> None:
        self.status.set_static(text)
        self.status.setVisible(bool(text))

    def set_foot(self, text: str) -> None:
        self.foot.setText(text)
        self.foot.setVisible(bool(text))


class AskScreen(QWidget):
    def __init__(self, window):
        super().__init__()
        self.win = window
        self.state = window.state
        self.chat = getattr(window, "chat_client", None)
        self._busy = False
        self._cold = False
        self._via = ""
        self._acc = ""
        self._messages: list[dict] = []
        self._conversations: list[dict] | None = None
        self._title = ""
        self._pending: _Bubble | None = None
        self._holder: QWidget | None = None
        self._narrow = False
        self._list_open = False
        # The page a question came from. While this screen is being built the
        # window hasn't switched yet, so current_key() is still the old page.
        cur = window.current_key() if hasattr(window, "current_key") else None
        self._from_key = cur if cur not in (None, "ask") else None
        stack = getattr(window, "stack", None)
        if stack is not None:
            stack.currentChanged.connect(self._track_page)

        root = hbox(self, (0, 0, 0, 0), 0)

        # ---- conversation list ----------------------------------------------
        self.list_col = QFrame()
        self.list_col.setProperty("role", "toolbar")
        self.list_col.setFixedWidth(LIST_W)
        lc = vbox(self.list_col, (T.S4, T.S6, T.S3, T.S4), T.S3)
        lc.addWidget(Eyebrow("Conversations"))
        self.new_btn = Button("New conversation", "secondary", icon="plus",
                              on_click=self._new)
        lc.addWidget(self.new_btn)
        self.list_scroll = ScrollArea(m=(0, 0, 0, 0), s=2)
        self.list_lay = self.list_scroll.lay
        lc.addWidget(self.list_scroll, 1)
        root.addWidget(self.list_col)
        self.list_rule = Divider(vertical=True)
        root.addWidget(self.list_rule)

        # ---- thread ----------------------------------------------------------
        pane = QWidget()
        pv = vbox(pane, (T.S8, T.S6, T.S8, T.S5), T.S4)
        self.header = ScreenHeader(
            "Ask Lumen", "Ask about your mail, calendar, todos, books or files.")
        self.list_btn = IconButton("list", "Show conversations", checkable=True,
                                   on_click=self._toggle_list)
        self.list_btn.hide()
        self.header.add_action(self.list_btn)
        self.model_badge = Badge("success", "")
        self.header.add_action(self.model_badge)
        pv.addWidget(self.header)

        self.thread_scroll = ScrollArea(m=(0, T.S2, T.S2, T.S2), s=T.S4)
        self.thread_lay = self.thread_scroll.lay
        pv.addWidget(self.thread_scroll, 1)
        vsb = self.thread_scroll.verticalScrollBar()
        vsb.rangeChanged.connect(lambda _lo, hi: self._busy and vsb.setValue(hi))

        comp = QFrame()
        comp.setProperty("role", "card")
        cv = vbox(comp, (T.S3, T.S3, T.S3, T.S3), T.S2)
        crow = hbox(s=T.S3)
        self.input = _Composer(self._send)
        self.input.setProperty("role", "bare")
        crow.addWidget(self.input, 1)
        self.send_btn = Button("Send", "primary", icon="send",
                               on_click=self._send)
        crow.addWidget(self.send_btn, 0, Qt.AlignmentFlag.AlignBottom)
        cv.addLayout(crow)
        hint = hbox(s=T.S1)
        hint.addWidget(Kbd("Enter"))
        hint.addWidget(Label("to send,", "caption"))
        hint.addWidget(Kbd("Shift+Enter"))
        hint.addWidget(Label("for a new line", "caption"))
        hint.addStretch(1)
        self.privacy = Label("", "caption")
        hint.addWidget(self.privacy)
        cv.addLayout(hint)
        pv.addWidget(comp)
        root.addWidget(pane, 1)

        # ---- stream wiring (same signals as ui_v3 chat.py) -------------------
        if self.chat is not None:
            self.chat.chunk.connect(self._on_chunk)
            self.chat.done.connect(self._on_done)
            self.chat.error.connect(self._on_error)
            self.chat.tool_used.connect(self._on_tool)
            self.chat.cold_start.connect(self._on_cold)
            self.chat.model_off.connect(self._on_model_off)
            self.chat.conversation.connect(self._on_conversation)
            if hasattr(self.chat, "via"):
                self.chat.via.connect(self._on_via)
            if hasattr(self.chat, "claude_unavailable"):
                self.chat.claude_unavailable.connect(self._on_claude_unavailable)
            if hasattr(self.chat, "captured"):
                self.chat.captured.connect(self._on_captured)

        self._wake = QTimer(self)
        self._wake.setSingleShot(True)
        self._wake.setInterval(WAKE_THRESHOLD_MS)
        self._wake.timeout.connect(self._on_wake)

        self.state.model_state_changed.connect(self._sync_model_badge)
        self._sync_model_badge()
        self._render_list()
        self._render_thread()
        # Continue the app-wide thread if one is already active.
        if self.state.active_conv_id is not None:
            self.open_conversation(self.state.active_conv_id)
        else:
            self._refresh_list()

    # ---- window hooks ---------------------------------------------------------
    def on_shown(self, focus: bool = False, **_kw):
        self._refresh_list()
        if focus or not self._busy:
            self.input.setFocus()

    def refresh(self):
        self._refresh_list()

    def start(self, question: str, context=None) -> None:
        """A question from the Ask bar (or anywhere via window.ask): send it
        into the active conversation, carrying the page it came from."""
        question = (question or "").strip()
        if not question:
            return
        ctx = _context_dict(context, self._from_key)
        if self._busy:
            # One turn at a time on the chat connection; don't drop it.
            self.input.setPlainText(question)
            self.input.setFocus()
            self.win.show_toast("Lumen is still answering. Your question is "
                                "ready to send when it's done.")
            return
        self._send_text(question, ctx, from_bar=True)

    def open_conversation(self, conv_id) -> None:
        self.load_conversation(conv_id)

    def load_conversation(self, cid) -> None:
        if cid is None:
            return
        if self._busy and cid != self.state.active_conv_id:
            self.win.show_toast("Wait for Lumen to finish answering first.")
            return
        self.state.active_conv_id = cid
        self._close_list_if_narrow()

        def done(result):
            conv = (result or {}).get("conversation") or {}
            if self.state.active_conv_id != cid or self._busy:
                return                 # the user moved on, or a turn is live
            self._title = conv.get("title") or "Conversation"
            self._messages = (result or {}).get("messages") or []
            self._render_thread()
            self._render_list()
        self.state.get_conversation(cid, done)
        self._render_list()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        narrow = self.width() < NARROW
        if narrow != self._narrow:
            self._narrow = narrow
            self.list_btn.setVisible(narrow)
            self._list_open = False
            self.list_btn.setChecked(False)
            self._apply_list_visibility()

    def _track_page(self, _index: int):
        """Remember the last non-Ask page, so an Ask-bar question can say
        where it came from even when its context is plain text."""
        key = self.win.current_key() if hasattr(self.win, "current_key") else None
        if key and key != "ask":
            self._from_key = key

    # ---- header -----------------------------------------------------------------
    def _sync_model_badge(self):
        s = self.state
        mode = getattr(s, "model_mode", "local")
        if not s.model_enabled or mode == "off":
            self.model_badge.set_kind("neutral", "Model off")
            self.privacy.setText("")
        elif mode == "claude":
            cm = getattr(s, "model_claude_model", "haiku") or "haiku"
            names = {"haiku": "Haiku", "sonnet": "Sonnet"}
            self.model_badge.set_kind("info", f"Claude · {names.get(cm, cm)}")
            self.privacy.setText("Answers come from Claude")
        else:
            self.model_badge.set_kind("success", "On this computer")
            self.privacy.setText("Answers stay on this computer")
        self.model_badge.adjustSize()

    def _set_title(self, title: str):
        self._title = title
        if title:
            self.header.set_subtitle(title)
        else:
            self.header.set_subtitle("Ask about your mail, calendar, todos, "
                                     "books or files.")

    # ---- conversation list ------------------------------------------------------
    def _toggle_list(self):
        self._list_open = self.list_btn.isChecked()
        self._apply_list_visibility()

    def _apply_list_visibility(self):
        show = (not self._narrow) or self._list_open
        self.list_col.setVisible(show)
        self.list_rule.setVisible(show)
        self.list_btn.set_tooltip("Hide conversations" if self._list_open
                                  else "Show conversations")

    def _close_list_if_narrow(self):
        if self._narrow and self._list_open:
            self._list_open = False
            self.list_btn.setChecked(False)
            self._apply_list_visibility()

    def _refresh_list(self):
        self.state.list_conversations(self._set_conversations)

    def _set_conversations(self, rows):
        self._conversations = rows if isinstance(rows, list) else []
        self._render_list()

    def _render_list(self):
        clear_layout(self.list_lay)
        if self._conversations is None:
            if self.state.live:
                for _ in range(4):
                    self.list_lay.addWidget(SkeletonRow(2, height=56))
                self.list_lay.addStretch(1)
                return
            self._conversations = []
        if not self._conversations:
            empty = QWidget()
            ev = vbox(empty, (T.S2, T.S4, T.S2, T.S4), T.S2)
            ev.addWidget(Label("No conversations yet", "body"))
            ev.addWidget(Label(
                "Saved conversations show up here. Ask something to start "
                "one." if self.state.live else
                "Sample mode doesn't save conversations.", "muted", wrap=True))
            self.list_lay.addWidget(empty)
            self.list_lay.addStretch(1)
            return
        active = self.state.active_conv_id
        for c in self._conversations:
            self.list_lay.addWidget(self._conv_row(c, c.get("id") == active))
        self.list_lay.addStretch(1)

    def _conv_row(self, c: dict, on: bool) -> QWidget:
        cid = c.get("id")
        title = c.get("title") or "Untitled"
        row = ClickRow(lambda: self.load_conversation(cid), selected=on,
                       accessible_name=f"Conversation: {title}")
        h = hbox(row, (T.S3, T.S2, T.S1, T.S2), T.S2)
        col = vbox(s=2)
        col.addWidget(ElideLabel(title, "body"))
        if c.get("preview"):
            col.addWidget(ElideLabel(c["preview"], "muted"))
        when = _when(c.get("updated_at"))
        if when:
            col.addWidget(Label(when, "meta"))
        h.addLayout(col, 1)
        h.addWidget(IconButton("trash", f"Delete “{title}”", size=32,
                               icon_size=T.ICON_SM, color="muted",
                               on_click=lambda: self._delete(cid, title)),
                    0, Qt.AlignmentFlag.AlignTop)
        return row

    def _delete(self, cid, title: str):
        """Local-only data, so no confirm ritual (same as ui_v3)."""
        if self._busy and self.state.active_conv_id == cid:
            self.win.show_toast("Wait for Lumen to finish answering first.")
            return
        self.state.delete_conversation(cid, lambda _r: self._refresh_list())
        if self._conversations:
            self._conversations = [c for c in self._conversations
                                   if c.get("id") != cid]
        if self.state.active_conv_id == cid:
            self.state.active_conv_id = None
            self._messages = []
            self._set_title("")
            self._render_thread()
        self._render_list()
        self.win.show_toast(f"Deleted “{title}”")

    def _new(self):
        if self._busy:
            self.win.show_toast("Wait for Lumen to finish answering first.")
            return
        self.state.active_conv_id = None
        self._messages = []
        self._set_title("")
        self._render_thread()
        self._render_list()
        self._close_list_if_narrow()
        self.input.setFocus()

    # ---- thread -----------------------------------------------------------------
    def _render_thread(self):
        clear_layout(self.thread_lay)
        self._pending = None
        self._holder = None
        self._set_title(self._title if self._messages else "")
        if not self._messages:
            if not self.state.model_enabled:
                self.thread_lay.addWidget(ModelOffNotice(
                    self.state, "Lumen still has your mail, calendar and "
                                "todos. Only the answering needs the model."))
            self.thread_lay.addWidget(EmptyState(
                "ask", "Start a conversation",
                "Ask about your day, an email, a file, or anything else. "
                "Everything you ask here stays in one conversation until you "
                "start a new one.",
                "Write a question", self.input.setFocus), 1)
            return
        for m in self._messages:
            self.thread_lay.addWidget(self._row(
                _Bubble(m.get("role", "assistant"),
                        m.get("text") or m.get("content", "")),
                m.get("role") == "user"))
        self.thread_lay.addStretch(1)

    @staticmethod
    def _row(bubble: QWidget, right: bool) -> QWidget:
        wrap = QWidget()
        h = hbox(wrap, (0, 0, 0, 0), 0)
        if right:
            h.addStretch(1)
        h.addWidget(bubble, 20)
        if not right:
            h.addStretch(1)
        return wrap

    def _open_thread_for(self):
        """Prepare the layout for more rows: drop the empty state, or the
        trailing stretch."""
        if not self._messages:
            clear_layout(self.thread_lay)
        elif self.thread_lay.count():
            last = self.thread_lay.itemAt(self.thread_lay.count() - 1)
            if last is not None and last.spacerItem() is not None:
                self.thread_lay.takeAt(self.thread_lay.count() - 1)

    def _append_user(self, msg: str, ctx: dict | None = None):
        self._open_thread_for()
        self._messages.append({"role": "user", "text": msg})
        note = ""
        screen = (ctx or {}).get("screen")
        if screen and screen in _SCREEN_NAMES:
            note = f"Asked from {_SCREEN_NAMES[screen]}"
            f = (ctx or {}).get("file")
            if screen == "files" and f:
                note += f" · {f.rsplit('/', 1)[-1]}"
        self.thread_lay.addWidget(self._row(_Bubble("user", msg, note), True))

    def _append_widget(self, w: QWidget):
        self.thread_lay.addWidget(w)

    def _end_rows(self):
        self.thread_lay.addStretch(1)

    # ---- sending ------------------------------------------------------------------
    def _send(self):
        msg = self.input.toPlainText().strip()
        if not msg or self._busy:
            return
        self._send_text(msg, None, from_bar=False)

    def _send_text(self, msg: str, ctx: dict | None, from_bar: bool):
        # Answered locally when the switch is off: no daemon round trip needed,
        # and the question still joins the thread so it's clear what went
        # unanswered.
        if not self.state.model_enabled:
            if not self._messages:
                self._set_title(msg[:60])
            self._append_user(msg, ctx)
            self._append_widget(ModelOffNotice(
                self.state, "Lumen still has your mail, calendar and todos. "
                            "Only the answering needs the model."))
            self._end_rows()
            self.input.clear()
            return
        if self.chat is None:
            self.win.show_toast("Lumen's background service isn't connected, "
                                "so there's no one to answer.")
            return
        self._busy = True
        self._cold = False
        self._via = ""
        self._acc = ""
        self.input.clear()
        self.send_btn.set_busy(True, "Answering…")
        if not self._messages:
            self._set_title(msg[:60])
        self._append_user(msg, ctx)

        bub = _Bubble("assistant")
        self._pending = bub
        self._holder = self._row(bub, False)
        self._append_widget(self._holder)
        self._end_rows()
        bub.status.show()
        bub.status.start("Thinking")
        self._wake.start()

        payload = {"message": msg}
        if ctx:
            payload["context"] = ctx
            if from_bar:
                payload["capture_ok"] = True
            # Name the open email so "label this email" resolves (ui_v3 #11).
            m = ctx.get("message") if isinstance(ctx.get("message"), dict) else None
            if ctx.get("screen") == "mail" and m and m.get("id"):
                payload["open_email"] = {"id": m["id"],
                                         "subject": m.get("subject"),
                                         "from": m.get("from")}
        if self.state.active_conv_id is not None:
            payload["conversation_id"] = self.state.active_conv_id
        self.chat.send("chat", payload)

    def _settle(self):
        self._busy = False
        self._wake.stop()
        self.send_btn.set_busy(False)

    def _drop_pending(self):
        if self._holder is not None:
            self._holder.hide()
            self._holder.setParent(None)
            self._holder.deleteLater()
        self._holder = None
        self._pending = None
        self._open_thread_for()

    # ---- stream handlers ------------------------------------------------------------
    def _status(self, text: str):
        if self._pending is not None:
            self._pending.set_status(text)

    def _on_cold(self):
        if self._busy and getattr(self.state, "model_mode", "local") != "claude":
            self._cold = True

    def _on_wake(self):
        # Only call it a cold start if the daemon said the model is loading
        # (ui_v3 #22); otherwise it's a slow prompt on a warm model.
        if not self._busy or self._pending is None:
            return
        self._pending.status.start("Waking the model — the first answer takes "
                                   "a little longer" if self._cold
                                   else "Working")

    def _on_conversation(self, cid: int):
        if self._busy:
            self.state.active_conv_id = cid

    def _on_tool(self, name: str):
        if self._busy and self._pending is not None:
            self._pending.tool.setText(f"Used {name.replace('_', ' ')}")
            self._pending.tool.show()

    def _on_via(self, tag: str):
        if self._busy:
            self._via = tag

    def _on_chunk(self, text: str):
        if not self._busy or self._pending is None:
            return
        self._wake.stop()
        self._pending.set_status("")
        self._acc += text
        self._pending.set_text(self._acc)

    def _on_done(self):
        if not self._busy:
            return
        self._settle()
        bub = self._pending
        if bub is not None:
            if not self._acc:
                # Settle the dots when a turn ends with no text (ui_v3 #34).
                bub.set_status(NO_REPLY_STATUS)
                bub.set_text(NO_REPLY_TEXT)
            else:
                bub.set_status("")
            bub.set_foot(self._via if self._via else
                         f"Answered on this computer · {T.MODEL_NAME}")
        self._messages.append({"role": "assistant", "text": self._acc})
        self._pending = None
        self._holder = None
        self._refresh_list()
        # A turn may have added or ticked off todos (ui_v3 #20).
        self.state.refresh_todos()

    def _on_error(self, msg: str):
        if not self._busy:
            return
        self._settle()
        if self._pending is not None:
            self._pending.set_status("Something went wrong")
            self._pending.status.set_role("error")
            self._pending.set_text(msg)
        self._pending = None
        self._holder = None

    def _on_model_off(self):
        """The switch moved after this turn started: swap the pending bubble
        for the notice."""
        if not self._busy:
            return
        self._settle()
        self._drop_pending()
        self._append_widget(ModelOffNotice(self.state))
        self._end_rows()

    def _on_claude_unavailable(self, _reason: str, message: str):
        if not self._busy:
            return
        self._settle()
        self._drop_pending()
        self._append_widget(ClaudeUnavailableNotice(self.state, message))
        self._end_rows()

    def _on_captured(self, todo: dict):
        """An Ask-bar note became a todo instead of a chat turn."""
        if not self._busy or self._pending is None:
            return
        text = (todo or {}).get("text") or "your note"
        self._pending.set_status("")
        self._acc = f"Added to your todos: {text}"
        self._pending.set_text(self._acc)
        row = QWidget()
        rh = hbox(row, (0, 0, 0, 0), T.S2)
        rh.addWidget(IconLabel("check-square", "accent", T.ICON_SM))
        rh.addWidget(Button("Open Todos", "ghost", size="sm",
                            on_click=lambda: self.win.switch_to("todos")))
        rh.addStretch(1)
        self._pending.layout().addWidget(row)
        self.state.refresh_todos()
