"""The Ask bar at the foot of each page, and its inline answer panel.

Josh's call (2026-10-10): a question asked from the bar is answered right
there on the page, like ui_v3's bar — it is ephemeral (#24: the daemon creates
no conversation and saves nothing), and "Continue in Ask Lumen" seeds a real
conversation from the exchange only when asked. The Ask Lumen screen keeps the
saved, multi-turn conversations.

The bar and the Ask screen share one chat connection, so only one of them
streams at a time (`LumenWindow.chat_busy()`); each ignores stream events while
it isn't the one asking.

`context_dict` / `chat_payload` are shared with the Ask screen so the page
context — including which email is open (ui_v3 #11) — is built in one place.
"""
from PyQt6.QtCore import QEvent, Qt, QTimer
from PyQt6.QtWidgets import QFrame, QLabel, QLineEdit, QWidget

from . import theme as T
from .components import (
    NO_REPLY_STATUS, NO_REPLY_TEXT, Button, ClaudeUnavailableNotice,
    IconButton, IconLabel, Label, ModelOffNotice, ScrollArea, TypingDots,
    clear_layout, hbox, set_prop, vbox,
)

WAKE_THRESHOLD_MS = 1500
# Fixed, as in ui_v3: a panel that grew with each chunk would shove the input
# around while the answer is being read.
PANEL_H = 220
SCREEN_NAMES = {"today": "Today", "mail": "Inbox", "calendar": "Calendar",
                "todos": "Todos", "books": "Books", "files": "Files",
                "canvas": "Canvas", "settings": "Settings"}


def context_dict(context, from_key: str | None) -> dict:
    """The daemon reads `context` as a dict ({screen, file/dir, ...}). Screens
    may hand back a dict or a plain-text summary."""
    if isinstance(context, dict):
        ctx = dict(context)
        if from_key and not ctx.get("screen"):
            ctx["screen"] = from_key
        return ctx
    ctx = {"screen": from_key} if from_key else {}
    if isinstance(context, str) and context.strip():
        ctx["summary"] = context.strip()[:8000]
    return ctx


def chat_payload(msg: str, ctx: dict | None, *, capture_ok: bool = False,
                 ephemeral: bool = False) -> dict:
    """The `chat` request body. Names the open email when the question comes
    from Inbox with a message open, so "label this email" resolves to a real
    id (ui_v3 #11)."""
    payload = {"message": msg}
    if ctx:
        payload["context"] = ctx
        m = ctx.get("message") if isinstance(ctx.get("message"), dict) else None
        if ctx.get("screen") == "mail" and m and m.get("id"):
            payload["open_email"] = {"id": m["id"],
                                     "subject": m.get("subject"),
                                     "from": m.get("from")}
    if capture_ok:
        payload["capture_ok"] = True
    if ephemeral:
        payload["ephemeral"] = True
    return payload


class AskBar(QFrame):
    """Bottom of the content area: the input row, with the answer panel
    opening above it."""

    def __init__(self, window):
        super().__init__()
        self.win = window
        self.state = window.state
        self.chat = getattr(window, "chat_client", None)
        self.busy = False
        self._cold = False
        self._via = ""
        self._acc = ""
        self._question = ""
        self._tools: list[str] = []
        self._captured = False        # the question became a todo instead
        self.setObjectName("askbar")
        root = vbox(self, (0, 0, 0, 0), 0)

        # ---- answer panel (hidden until there's something to show) --------
        self.panel = QFrame()
        self.panel.setObjectName("askPanel")
        self.panel.setFixedHeight(PANEL_H)
        pv = vbox(self.panel, (0, 0, 0, 0), 0)
        self.panel_scroll = ScrollArea(m=(T.S6, T.S4, T.S6, T.S4), s=T.S2)
        self.panel_lay = self.panel_scroll.lay
        pv.addWidget(self.panel_scroll)
        self.panel.hide()
        root.addWidget(self.panel)
        self.status = self.answer = self.tool = self.foot = None
        self.open_btn = None

        # ---- input row ------------------------------------------------------
        row = QWidget()
        h = hbox(row, (T.S6, T.S3, T.S6, T.S3), T.S3)
        self.label = QLabel("Ask Lumen")
        self.label.setObjectName("askLabel")
        h.addWidget(self.label)
        self.field = QFrame()
        self.field.setObjectName("askField")
        fh = hbox(self.field, (T.S3, 4, 4, 4), T.S2)
        fh.addWidget(IconLabel("ask", "muted", T.ICON_SM))
        self.input = QLineEdit()
        self.input.setProperty("role", "bare")
        self.input.setPlaceholderText("Ask about this page…")
        self.input.setAccessibleName("Ask Lumen")
        self.input.returnPressed.connect(self.submit)
        self.input.installEventFilter(self)
        self.label.setBuddy(self.input)
        fh.addWidget(self.input, 1)
        self.send = IconButton("arrow-up", "Ask", size=32, variant="primary",
                               on_click=self.submit)
        fh.addWidget(self.send)
        h.addWidget(self.field, 1)
        root.addWidget(row)

        if self.chat is not None:
            self.chat.chunk.connect(self._on_chunk)
            self.chat.done.connect(self._on_done)
            self.chat.error.connect(self._on_error)
            self.chat.tool_used.connect(self._on_tool)
            self.chat.cold_start.connect(self._on_cold)
            self.chat.model_off.connect(self._on_model_off)
            for name, slot in (("via", self._on_via),
                               ("claude_unavailable", self._on_claude_unavailable),
                               ("captured", self._on_captured)):
                sig = getattr(self.chat, name, None)
                if sig is not None:
                    sig.connect(slot)

        self._wake = QTimer(self)
        self._wake.setSingleShot(True)
        self._wake.setInterval(WAKE_THRESHOLD_MS)
        self._wake.timeout.connect(self._on_wake)

    # ---- input --------------------------------------------------------------
    def eventFilter(self, obj, ev):
        if obj is self.input:
            if ev.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
                set_prop(self.field, "focused", ev.type() == QEvent.Type.FocusIn)
            elif (ev.type() == QEvent.Type.KeyPress
                  and ev.key() == Qt.Key.Key_Escape and self.panel.isVisible()):
                self.dismiss()
                return True
        return False

    def focus(self):
        self.input.setFocus()
        self.input.selectAll()

    def set_collapsed(self, on: bool) -> None:
        self.label.setVisible(not on)

    def submit(self):
        q = self.input.text().strip()
        if q:
            self.ask(q, self.win.current_ask_context())

    def ask(self, question: str, context=None) -> None:
        """Answer `question` in the panel, with the page's context."""
        question = (question or "").strip()
        if not question:
            return
        if self.win.chat_busy():
            # One turn at a time on the chat connection; keep the question.
            self.input.setText(question)
            self.win.show_toast("Lumen is still answering. Ask again when "
                                "it's done.")
            return
        self.input.clear()
        self._question = question
        if not self.state.model_enabled:
            self._show_notice(ModelOffNotice(self.state))
            return
        if self.chat is None:
            self.win.show_toast("Lumen's background service isn't connected, "
                                "so there's no one to answer.")
            return
        self.busy = True
        self._cold = False
        self._via = ""
        self._acc = ""
        self._tools = []
        self._captured = False
        self._open_panel()
        self._wake.start()
        ctx = context_dict(context, self.win.current_key())
        # Ephemeral (#24): no conversation is created or saved. capture_ok
        # keeps ui_v3's note-shaped question -> todo.
        self.chat.send("chat", chat_payload(question, ctx, capture_ok=True,
                                            ephemeral=True))

    # ---- panel --------------------------------------------------------------
    def _head(self, status: bool) -> None:
        head = hbox(s=T.S2)
        if status:
            self.status = TypingDots("Thinking", "muted")
            head.addWidget(self.status)
        else:
            self.status = None
        head.addStretch(1)
        self.open_btn = Button("Continue in Ask Lumen", "ghost", icon="ask",
                               size="sm", on_click=self._open_in_ask)
        self.open_btn.hide()
        head.addWidget(self.open_btn)
        head.addWidget(IconButton("x", "Dismiss (Esc)", size=32,
                                  icon_size=T.ICON_SM, color="muted",
                                  on_click=self.dismiss))
        self.panel_lay.addLayout(head)
        q = Label(self._question, "muted", wrap=True, selectable=True)
        q.setTextFormat(Qt.TextFormat.PlainText)
        self.panel_lay.addWidget(q)

    def _open_panel(self) -> None:
        clear_layout(self.panel_lay)
        self._head(status=True)
        self.tool = Label("", "meta")
        self.tool.hide()
        self.panel_lay.addWidget(self.tool)
        self.answer = Label("", "body", wrap=True, selectable=True)
        self.answer.setTextFormat(Qt.TextFormat.PlainText)
        self.panel_lay.addWidget(self.answer)
        self.foot = Label("", "caption")
        self.foot.hide()
        self.panel_lay.addWidget(self.foot)
        self.panel_lay.addStretch(1)
        self.panel.show()
        self.status.start("Thinking")

    def _show_notice(self, notice: QWidget) -> None:
        """A notice in the place the answer would have gone, so nothing moves."""
        clear_layout(self.panel_lay)
        self._head(status=False)
        self.answer = self.tool = self.foot = None
        self.panel_lay.addWidget(notice)
        self.panel_lay.addStretch(1)
        self.panel.show()

    def dismiss(self) -> None:
        if self.busy:
            return                  # the answer is still coming; keep it visible
        self.panel.hide()
        clear_layout(self.panel_lay)
        self.status = self.answer = self.tool = self.foot = None
        self.open_btn = None

    def _open_in_ask(self) -> None:
        """Seed a real conversation from this exchange and open it (#24)."""
        q, a = self._question, self._acc
        if not q or not a:
            return

        def opened(cid):
            if cid is None:
                self.win.show_toast("Sample mode doesn't save conversations.")
                return
            self.state.active_conv_id = cid
            self.dismiss()
            self.state.open_chat_requested.emit(cid)
        self.state.seed_conversation(q, a, self._tools, opened)

    def _settle(self) -> None:
        self.busy = False
        self._wake.stop()

    # ---- stream handlers ------------------------------------------------------
    def _on_cold(self):
        if self.busy and getattr(self.state, "model_mode", "local") != "claude":
            self._cold = True

    def _on_wake(self):
        # Only call it a cold start if the daemon said the model is loading
        # (ui_v3 #22); otherwise it's a slow prompt on a warm model.
        if self.busy and self.status is not None:
            self.status.start("Waking the model — the first answer takes a "
                              "little longer" if self._cold else "Working")

    def _on_via(self, tag: str):
        if self.busy:
            self._via = tag

    def _on_tool(self, name: str):
        if self.busy and self.tool is not None:
            self._tools.append(name)
            self.tool.setText(f"Used {name.replace('_', ' ')}")
            self.tool.show()

    def _on_chunk(self, text: str):
        if not self.busy or self.answer is None:
            return
        self._wake.stop()
        if self.status is not None:
            self.status.set_static("")
            self.status.hide()
        self._acc += text
        self.answer.setText(self._acc)
        vsb = self.panel_scroll.verticalScrollBar()
        vsb.setValue(vsb.maximum())

    def _on_done(self):
        if not self.busy:
            return
        self._settle()
        if self.status is not None:
            if self._acc:
                self.status.hide()
            else:
                # Settle the dots when a turn ends with no text (ui_v3 #34).
                self.status.set_static(NO_REPLY_STATUS)
                self.status.show()
        if self.answer is not None and not self._acc:
            self.answer.setText(NO_REPLY_TEXT)
        if self.foot is not None:
            self.foot.setText(self._via or "Answered on this computer · not saved")
            self.foot.show()
        if self._acc and not self._captured and self.open_btn is not None:
            self.open_btn.show()
        # A turn may have added or ticked off todos (ui_v3 #20).
        self.state.refresh_todos()

    def _on_error(self, msg: str):
        if not self.busy:
            return
        self._settle()
        if self.status is not None:
            self.status.set_static("Something went wrong", "error")
            self.status.show()
        if self.answer is not None:
            self.answer.setText(msg)

    def _on_model_off(self):
        """The switch moved after this question was sent."""
        if not self.busy:
            return
        self._settle()
        self._show_notice(ModelOffNotice(self.state))

    def _on_claude_unavailable(self, _reason: str, message: str):
        if not self.busy:
            return
        self._settle()
        self._show_notice(ClaudeUnavailableNotice(self.state, message))

    def _on_captured(self, todo: dict):
        """A note-shaped question became a todo instead of an answer."""
        if not self.busy or self.answer is None:
            return
        if self.status is not None:
            self.status.hide()
        text = (todo or {}).get("text") or "your note"
        # _acc, so the done that follows doesn't read it as "no answer".
        self._acc = f"Added to your todos: {text}"
        self._captured = True
        self.answer.setText(self._acc)
        row = QWidget()
        rh = hbox(row, (0, 0, 0, 0), T.S2)
        rh.addWidget(IconLabel("check-square", "accent", T.ICON_SM))
        rh.addWidget(Button("Open Todos", "ghost", size="sm",
                            on_click=lambda: self.win.switch_to("todos")))
        rh.addStretch(1)
        self.panel_lay.insertWidget(self.panel_lay.indexOf(self.answer) + 1, row)
        self.state.refresh_todos()
