"""Full Chat screen: a Claude-style history sidebar + the running conversation.

Renders turns and sends messages only — the daemon owns thread state, storage,
and history truncation. Streaming reuses the shared chat client; the sidebar and
thread come from the daemon's conversations.list / conversations.get.
"""
from PyQt6.QtWidgets import QFrame, QLineEdit, QWidget

from .. import theme as T
from ..state import AppState
from ..widgets import (
    ClickRow, button, clear_layout, hbox, hline, label, scroll, vbox, vline,
)


class ChatScreen(QWidget):
    def __init__(self, state: AppState, chat_client=None):
        super().__init__()
        self.state = state
        self.chat = chat_client if chat_client is not None else state._chat
        self._conv_id = None
        self._busy = False
        self._acc = ""
        self.resp_text = None      # current assistant bubble

        root = hbox(self, (0, 0, 0, 0), 0)
        root.addWidget(self._build_sidebar())
        root.addWidget(vline(T.BORDER_SOFT))
        root.addWidget(self._build_main(), 1)

        if self.chat is not None:      # sample mode (screenshots) has no daemon
            self.chat.chunk.connect(self._on_chunk)
            self.chat.done.connect(self._on_done)
            self.chat.error.connect(self._on_error)
            self.chat.tool_used.connect(self._on_tool)
            self.chat.conversation.connect(self._on_conversation)

        self.refresh_list()

    # ---- sidebar ---------------------------------------------------------
    def _build_sidebar(self) -> QWidget:
        side = QFrame()
        side.setFixedWidth(240)
        v = vbox(side, (12, 14, 12, 12), 10)
        newb = button("＋  New chat", "primary", px=12, height=34)
        newb.clicked.connect(self.new_chat)
        v.addWidget(newb)
        v.addWidget(label("HISTORY", 10, T.TEXT_FAINT, ls=1))
        self.list_host = QWidget()
        self.list_lay = vbox(self.list_host, (0, 0, 0, 0), 2)
        v.addWidget(scroll(self.list_host), 1)
        return side

    def refresh_list(self):
        self.state.list_conversations(self._set_list)

    def _set_list(self, rows: list[dict]):
        clear_layout(self.list_lay)
        for r in rows:
            row = ClickRow(lambda cid=r["id"]: self.load_conversation(cid))
            rl = hbox(row, (8, 7, 8, 7), 0)
            rl.addWidget(label(r.get("title") or "Untitled", 12, T.TEXT_SECONDARY), 1)
            self.list_lay.addWidget(row)
        self.list_lay.addStretch(1)

    # ---- main pane -------------------------------------------------------
    def _build_main(self) -> QWidget:
        main = QWidget()
        v = vbox(main, (0, 0, 0, 0), 0)
        self.thread = QWidget()
        self.thread_lay = vbox(self.thread, (26, 22, 26, 18), 14)
        # Trailing stretch absorbs leftover viewport height — without it the
        # vbox spreads a short thread across the whole window.
        self.thread_lay.addStretch(1)
        sa = scroll(self.thread)
        self._vsb = sa.verticalScrollBar()
        self._vsb.rangeChanged.connect(self._pin_bottom)
        v.addWidget(sa, 1)
        v.addWidget(hline(T.BORDER_SOFT))
        inrow = hbox(m=(20, 12, 20, 14), s=10)
        inrow.addWidget(label("❯", 16, T.ACCENT, 700))
        self.input = QLineEdit()
        self.input.setProperty("cls", "bare")
        self.input.setPlaceholderText("Message Lumen…")
        f = self.input.font()
        f.setPixelSize(15)
        self.input.setFont(f)
        self.input.returnPressed.connect(self._submit)
        inrow.addWidget(self.input, 1)
        v.addLayout(inrow)
        return main

    def _pin_bottom(self, _lo: int, hi: int):
        if self._busy:      # follow the stream; leave a browsing user alone
            self._vsb.setValue(hi)

    def _turn(self, who: str, who_color: str, text: str, text_color: str):
        w = QWidget()
        lay = vbox(w, (0, 0, 0, 0), 4)
        lay.addWidget(label(who, 10, who_color, ls=1))
        body = label(text, 14, text_color, sans=True, wrap=True)
        lay.addWidget(body)
        self.thread_lay.insertWidget(self.thread_lay.count() - 1, w)
        return body

    def _add_user_turn(self, text: str):
        self._turn("YOU", T.TEXT_FAINT, text, T.TEXT_SECONDARY)

    def _begin_assistant_turn(self):
        self._acc = ""
        self.tool_lab = label("", 10, T.TEXT_FAINT)
        self.tool_lab.hide()
        self.thread_lay.insertWidget(self.thread_lay.count() - 1, self.tool_lab)
        self.resp_text = self._turn("LUMEN", T.ACCENT, "", T.TEXT_PRIMARY)

    # ---- conversation lifecycle -----------------------------------------
    def new_chat(self):
        self._conv_id = None
        self._busy = False
        self.resp_text = None
        clear_layout(self.thread_lay)
        self.thread_lay.addStretch(1)   # clear_layout drops the stretch too
        self.input.setFocus()

    def load_conversation(self, cid: int):
        """Reopen a past thread from storage (also the overlay-handoff entry)."""
        self._conv_id = cid
        self.state.get_conversation(cid, self._render_thread)

    def _render_thread(self, got: dict):
        clear_layout(self.thread_lay)
        self.thread_lay.addStretch(1)   # clear_layout drops the stretch too
        self.resp_text = None
        for m in got.get("messages", []):
            if m["role"] == "user":
                self._add_user_turn(m["content"])
            else:
                self._turn("LUMEN", T.ACCENT, m["content"], T.TEXT_PRIMARY)

    def _submit(self):
        msg = self.input.text().strip()
        if self.chat is None or not msg or self._busy:
            return
        self._busy = True
        self._add_user_turn(msg)
        self._begin_assistant_turn()
        self.input.clear()
        payload = {"message": msg}
        if self._conv_id is not None:      # continue the loaded/ongoing thread
            payload["conversation_id"] = self._conv_id
        self.chat.send("chat", payload)

    # ---- streaming (busy-guarded so the shared client can't cross-talk) --
    def _on_conversation(self, cid: int):
        if self._busy:
            self._conv_id = cid

    def _on_tool(self, name: str):
        if self._busy and getattr(self, "tool_lab", None) is not None:
            self.tool_lab.setText(f"🔧 used {name}")
            self.tool_lab.show()

    def _on_chunk(self, text: str):
        if not self._busy:
            return
        self._acc += text
        self.resp_text.setText(self._acc)

    def _on_done(self):
        if not self._busy:
            return
        self._busy = False
        if not self._acc:
            self.resp_text.setText("(no answer)")
        self.refresh_list()      # a newly created thread now shows in the sidebar

    def _on_error(self, msg: str):
        if not self._busy:
            return
        self._busy = False
        if self.resp_text is not None:
            self.resp_text.setText(msg)
