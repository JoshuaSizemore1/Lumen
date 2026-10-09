"""Chat — conversation list beside a streaming thread.

Streams from the daemon exactly as the launcher palette does: chunks append to
the live bubble, tool use surfaces as a caption above it, and the thread id is
app-wide (`state.active_conv_id`) so the ask bar, the Super+L palette, and this
screen all continue the same conversation.
"""
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QFrame, QLineEdit, QWidget

from .. import theme as T
from ..components import accent_fill
from ..widgets import (
    NO_REPLY_STATUS, NO_REPLY_TEXT, ClickLabel, ClickRow, Dot, ElideLabel,
    TypingDots, button, clear_layout,
    empty_state, font, hbox, hline, label, scroll, vbox, vline,
)

WAKE_THRESHOLD_MS = 1500


class ChatScreen(QWidget):
    def __init__(self, state, chat_client=None):
        super().__init__()
        self.setObjectName("screen")
        self.state = state
        self.chat = chat_client
        self._busy = False
        self._cold = False    # set by the daemon when this turn actually loads the model
        self._acc = ""
        self._messages: list[dict] = []
        self._conversations: list[dict] = []
        self.resp_label = None
        self.tool_label = None
        self.status = None

        root = hbox(self, (0, 0, 0, 0), 0)

        # ---- conversation list -------------------------------------------
        col = QWidget()
        col.setFixedWidth(T.CHAT_LIST_W)
        cv = vbox(col, (0, 0, 0, 0), 0)
        newbar = hbox(m=(15, 16, 15, 11), s=0)
        new_b = button("+ New conversation", "soft", px=15, height=38)
        new_b.clicked.connect(self._new)
        newbar.addWidget(new_b)
        cv.addLayout(newbar)
        cv.addWidget(hline(T.BORDER_MED))
        self.list_host = QWidget()
        self.list_lay = vbox(self.list_host, (8, 9, 8, 9), 0)
        cv.addWidget(scroll(self.list_host), 1)
        root.addWidget(col)
        root.addWidget(vline(T.BORDER_MED))

        # ---- thread (greedy) ---------------------------------------------
        pane = QWidget()
        pv = vbox(pane, (0, 0, 0, 0), 0)
        self.head = QWidget()
        hrow = hbox(self.head, (24, 16, 24, 16), 10)
        # Never blank: the composer stays available with no thread selected
        # (typing starts a fresh one), so the header needs to say so.
        self.title = label("New conversation", 21, T.TEXT_PRIMARY, 500)
        hrow.addWidget(self.title)
        hrow.addStretch(1)
        model = hbox(s=6)
        model.addWidget(Dot(6, T.OK))
        model.addWidget(label(f"local · {T.MODEL_NAME}", 10, T.TEXT_MUTED,
                              mono=True))
        hrow.addLayout(model)
        pv.addWidget(self.head)
        pv.addWidget(hline(T.BORDER_MED))

        self.thread_host = QWidget()
        self.thread_lay = vbox(self.thread_host, (28, 24, 28, 24), 0)
        self.thread_lay.addStretch(1)
        self.thread_scroll = scroll(self.thread_host)
        pv.addWidget(self.thread_scroll, 1)
        vsb = self.thread_scroll.verticalScrollBar()
        vsb.rangeChanged.connect(lambda _lo, hi: self._busy and vsb.setValue(hi))

        pv.addWidget(hline(T.BORDER_MED))
        foot = hbox(m=(24, 16, 24, 16), s=11)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Message Lumen…")
        self.input.setFont(font(14.5))
        self.input.returnPressed.connect(self._send)
        foot.addWidget(self.input, 1)
        send = button("Send", "primary", px=15, height=40)
        send.clicked.connect(self._send)
        foot.addWidget(send)
        pv.addLayout(foot)
        root.addWidget(pane, 1)

        if self.chat is not None:
            self.chat.chunk.connect(self._on_chunk)
            self.chat.done.connect(self._on_done)
            self.chat.error.connect(self._on_error)
            self.chat.tool_used.connect(self._on_tool)
            self.chat.cold_start.connect(self._on_cold)
            self.chat.conversation.connect(self._on_conversation)

        self._wake = QTimer(self)
        self._wake.setSingleShot(True)
        self._wake.setInterval(WAKE_THRESHOLD_MS)
        self._wake.timeout.connect(self._on_wake)

        self._refresh_list()
        self._render_thread()

    def showEvent(self, ev):
        super().showEvent(ev)
        self.state.warm_model()
        self._refresh_list()

    # ---- conversation list ------------------------------------------------
    def _refresh_list(self):
        self.state.list_conversations(self._set_conversations)

    def _set_conversations(self, rows):
        self._conversations = rows or []
        clear_layout(self.list_lay)
        if not self._conversations:
            self.list_lay.addWidget(empty_state("No conversations yet"))
            self.list_lay.addStretch(1)
            return
        active = self.state.active_conv_id
        for c in self._conversations:
            self.list_lay.addWidget(self._conv_row(c, c["id"] == active))
        self.list_lay.addStretch(1)

    def _conv_row(self, c: dict, on: bool) -> QWidget:
        row = ClickRow(lambda: self.load_conversation(c["id"]))
        lay = hbox(row, (11, 11, 11, 11), 8)
        col = vbox(m=(0, 0, 0, 0), s=2)
        col.addWidget(ElideLabel(c.get("title") or "Untitled", 15,
                                 T.TEXT_PRIMARY if on else T.TEXT_SECONDARY,
                                 600 if on else 500))
        if c.get("preview"):
            col.addWidget(ElideLabel(c["preview"], 11, T.TEXT_MUTED))
        col.addWidget(label(str(c.get("updated_at") or ""), 9, T.TEXT_FAINTER,
                            mono=True))
        lay.addLayout(col, 1)
        lay.addWidget(ClickLabel("✕", 12, T.TEXT_GHOST, tooltip="Delete",
                                 on_click=lambda: self._delete(c["id"])))
        if on:
            row.setStyleSheet(
                f"ClickRow {{ background: {accent_fill()}; border-radius: 5px;"
                f" border-left: 2px solid {T.ACCENT}; }}")
        return row

    def _delete(self, cid: int):
        self.state.delete_conversation(cid, lambda _r: self._refresh_list())
        if self.state.active_conv_id == cid:
            self.state.active_conv_id = None
            self._messages = []
            self.title.setText("New conversation")
            self._render_thread()

    def _new(self):
        self.state.active_conv_id = None
        self._messages = []
        self.title.setText("New conversation")
        self._render_thread()
        self.input.setFocus()
        self._refresh_list()

    def load_conversation(self, cid: int):
        self.state.active_conv_id = cid

        def done(result):
            conv = (result or {}).get("conversation") or {}
            self.title.setText(conv.get("title") or "Conversation")
            self._messages = (result or {}).get("messages") or []
            self._render_thread()
            self._refresh_list()
        self.state.get_conversation(cid, done)

    # ---- thread -----------------------------------------------------------
    def _render_thread(self):
        clear_layout(self.thread_lay)
        if not self._messages:
            self.thread_lay.addWidget(empty_state(
                "Select a conversation, or start a new one"))
            self.thread_lay.addStretch(1)
            return
        for m in self._messages:
            self.thread_lay.addWidget(self._bubble(m.get("role", "assistant"),
                                                   m.get("text") or m.get("content", "")))
        self.thread_lay.addStretch(1)

    def _bubble(self, role: str, text: str) -> QWidget:
        is_user = role == "user"
        wrap = QWidget()
        row = hbox(wrap, (0, 0, 0, 16), 0)
        if is_user:
            row.addStretch(1)
        bub = QFrame()
        bub.setMaximumWidth(640)
        # Scoped by property, not by "QFrame": QLabel derives from QFrame, so a
        # bare QFrame rule paints a box around every line of text inside the
        # bubble as well as around the bubble.
        bub.setProperty("cls", "bubble")
        bub.setStyleSheet(
            f'QFrame[cls="bubble"] {{ background:'
            f" {accent_fill() if is_user else T.BG_FIELD};"
            f" border: 1px solid {T.ACCENT if is_user else T.BORDER_FIELD};"
            " border-radius: 10px; }")
        bv = vbox(bub, (15, 11, 15, 11), 6)
        bv.addWidget(label("YOU" if is_user else "LUMEN", 9,
                           T.ACCENT if is_user else T.TEXT_FAINT, mono=True,
                           ls=1.5))
        body = label(text, 15, T.TEXT_PRIMARY, wrap=True)
        bv.addWidget(body)
        row.addWidget(bub)
        if not is_user:
            row.addStretch(1)
        return wrap

    # ---- streaming --------------------------------------------------------
    def _send(self):
        msg = self.input.text().strip()
        if not msg or self._busy or self.chat is None:
            return
        self._busy = True
        self._cold = False
        self._acc = ""
        self.input.clear()
        if not self._messages:
            self.title.setText(msg[:40])

        if not self._messages:
            clear_layout(self.thread_lay)      # drop the empty-state placeholder
        else:
            self.thread_lay.takeAt(self.thread_lay.count() - 1)   # drop stretch
        self._messages.append({"role": "user", "text": msg})
        self.thread_lay.addWidget(self._bubble("user", msg))

        holder = QWidget()
        hv = vbox(holder, (0, 0, 0, 16), 6)
        self.tool_label = label("", 10, T.TEXT_FAINT, mono=True)
        self.tool_label.hide()
        hv.addWidget(self.tool_label)
        bub = QFrame()
        bub.setMaximumWidth(640)
        bub.setProperty("cls", "bubble")      # see _bubble: QLabel is a QFrame
        bub.setStyleSheet(
            f'QFrame[cls="bubble"] {{ background: {T.BG_FIELD};'
            f" border: 1px solid {T.BORDER_FIELD}; border-radius: 10px; }}")
        bv = vbox(bub, (15, 11, 15, 11), 6)
        self.status = TypingDots("◇ thinking", 9, T.INFO, ls=1.5)
        bv.addWidget(self.status)
        self.resp_label = label("", 15, T.TEXT_PRIMARY, wrap=True)
        bv.addWidget(self.resp_label)
        brow = hbox(s=0)
        brow.addWidget(bub)
        brow.addStretch(1)
        hv.addLayout(brow)
        self.thread_lay.addWidget(holder)
        self.thread_lay.addStretch(1)

        self.status.start("◇ thinking")
        self._wake.start()
        payload = {"message": msg}
        if self.state.active_conv_id is not None:
            payload["conversation_id"] = self.state.active_conv_id
        self.chat.send("chat", payload)

    def _status(self, text: str, color: str):
        if self.status is not None:
            self.status.set_static(text, color)

    def _on_cold(self):
        # The daemon says this turn genuinely loads the model — the wake message
        # may honestly say "cold start". Arrives well before the wake timer.
        if self._busy:
            self._cold = True

    def _on_wake(self):
        # Still no first token after the threshold. Only call it a cold start if
        # the daemon told us the model was actually loading (#22); otherwise it's
        # just a slow prompt-eval on an already-warm model.
        if self._cold:
            self._status("◇ waking model… (cold start)", T.WARN)
        else:
            self._status("◇ working…", T.INFO)

    def _on_conversation(self, cid: int):
        if self._busy:
            self.state.active_conv_id = cid

    def _on_tool(self, name: str):
        if self._busy and self.tool_label is not None:
            self.tool_label.setText(f"◆ used {name}")
            self.tool_label.show()

    def _on_chunk(self, text: str):
        if not self._busy:
            return
        self._wake.stop()
        self._status("LUMEN", T.TEXT_FAINT)
        self._acc += text
        self.resp_label.setText(self._acc)

    def _on_done(self):
        if not self._busy:
            return
        self._busy = False
        self._wake.stop()
        if not self._acc:
            # Settle the status so the "◇ thinking" dots stop when a turn ends
            # with no text — otherwise the spinner runs forever (#34).
            self._status(NO_REPLY_STATUS, T.TEXT_FAINT)
            self.resp_label.setText(NO_REPLY_TEXT)
        self._messages.append({"role": "assistant", "text": self._acc})
        self._refresh_list()
        # A turn may have run add_todo/complete_todo; pull fresh state so the
        # Todos screen reflects it without a manual add first (#20).
        self.state.refresh_todos()

    def _on_error(self, msg: str):
        if not self._busy:
            return
        self._busy = False
        self._wake.stop()
        self._status("⚠ error", T.WARN)
        if self.resp_label is not None:
            self.resp_label.setText(msg)
