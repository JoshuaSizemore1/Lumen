"""The "Ask Lumen — it can see this page" bar and its inline answer panel.

The mockup fakes this: a regex over the query picks one of five canned panels.
Here it is real. The bar collects the active screen's context (which day the
calendar is showing, which message is open, which file is in the editor),
sends it with the question, and streams the answer back into a panel that
expands above the input.

It shares `state.active_conv_id` with the Chat screen and the Super+L palette,
so "open in Chat ↗" continues the same thread rather than starting a new one.
"""
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QFrame, QLineEdit, QWidget

from . import theme as T
from .widgets import (
    Chip, ClickLabel, TypingDots, clear_layout, font, hbox, label, scroll,
    vbox,
)

WAKE_THRESHOLD_MS = 1500
# The mock expands the ask panel to a fixed 197px and scrolls inside it. Fixed
# is also the right call for streaming: a panel that grew with each chunk would
# shove the input around while the user is reading.
PANEL_H = 210

# Screens where the mock shows the bar. Chat has its own composer and Settings
# is a config sheet, so neither gets one.
ASK_SCREENS = ("today", "calendar", "mail", "todos", "books", "files")


class AskBar(QFrame):
    def __init__(self, state, chat_client, context_provider):
        """context_provider() -> dict describing what the user is looking at."""
        super().__init__()
        self.setObjectName("askbar")
        self.state = state
        self.chat = chat_client
        self._context = context_provider
        self._busy = False
        self._acc = ""

        root = vbox(self, (0, 0, 0, 0), 0)

        # ---- answer panel (hidden until there is something to show) -------
        self.panel = QFrame()
        self.panel.setObjectName("answerPanel")
        self.panel.setFixedHeight(T.sc(PANEL_H))
        pv = vbox(self.panel, (0, 0, 0, 0), 0)
        self.panel_host = QWidget()
        self.panel_lay = vbox(self.panel_host, (24, 16, 24, 16), 0)
        self.panel_scroll = scroll(self.panel_host)
        pv.addWidget(self.panel_scroll)
        self.panel.hide()
        root.addWidget(self.panel)

        # ---- input row ----------------------------------------------------
        row = hbox(m=(24, 12, 24, 12), s=11)
        row.addWidget(label("❯", 12, T.ACCENT, mono=True))
        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask Lumen — it can see this page…")
        self.input.setFont(font(14))
        self.input.returnPressed.connect(self.submit)
        row.addWidget(self.input, 1)
        row.addWidget(Chip("ON-DEVICE", T.TEXT_FAINT, T.BORDER_STRONG, px=9,
                           hpad=8, vpad=3, ls=1))
        root.addLayout(row)

        if self.chat is not None:
            self.chat.chunk.connect(self._on_chunk)
            self.chat.done.connect(self._on_done)
            self.chat.error.connect(self._on_error)
            self.chat.tool_used.connect(self._on_tool)
            self.chat.conversation.connect(self._on_conversation)

        self._wake = QTimer(self)
        self._wake.setSingleShot(True)
        self._wake.setInterval(WAKE_THRESHOLD_MS)
        self._wake.timeout.connect(
            lambda: self._status("◇ waking model… (cold start)", T.WARN))

    def focus(self):
        self.input.setFocus()

    # ---- submit -----------------------------------------------------------
    def submit(self):
        msg = self.input.text().strip()
        if not msg or self._busy or self.chat is None:
            return
        self._busy = True
        self._acc = ""
        self.input.clear()
        self._open_panel(msg)
        self._wake.start()

        payload = {"message": msg, "context": self._context() or {}}
        if self.state.active_conv_id is not None:
            payload["conversation_id"] = self.state.active_conv_id
        else:
            payload["capture_ok"] = True   # note-shaped text may become a todo
        self.chat.send("chat", payload)

    def _open_panel(self, question: str):
        clear_layout(self.panel_lay)
        self.panel.show()

        head = hbox(s=8)
        self.status = TypingDots("◇ thinking", 10, T.INFO, ls=1.5)
        head.addWidget(self.status)
        head.addStretch(1)
        self.open_chat = ClickLabel("open in Chat ↗", 10, T.ACCENT, mono=True,
                                    on_click=self._open_in_chat)
        self.open_chat.hide()
        head.addWidget(self.open_chat)
        head.addWidget(ClickLabel("✕", 11, T.TEXT_GHOST, on_click=self.dismiss,
                                  tooltip="Dismiss"))
        self.panel_lay.addLayout(head)
        self.panel_lay.addSpacing(11)

        self.panel_lay.addWidget(label(question, 13, T.TEXT_MUTED, wrap=True))
        self.panel_lay.addSpacing(8)

        self.tool_label = label("", 10, T.TEXT_FAINT, mono=True)
        self.tool_label.hide()
        self.panel_lay.addWidget(self.tool_label)

        self.answer = label("", 15, T.TEXT_PRIMARY, wrap=True)
        self.panel_lay.addWidget(self.answer)

        self.foot = label("", 10, T.TEXT_FAINT, mono=True)
        self.panel_lay.addSpacing(12)
        self.panel_lay.addWidget(self.foot)
        self.panel_lay.addStretch(1)
        self.status.start("◇ thinking")

    def dismiss(self):
        self.panel.hide()
        clear_layout(self.panel_lay)

    def _open_in_chat(self):
        if self.state.active_conv_id is not None:
            self.state.open_chat_requested.emit(self.state.active_conv_id)

    def _status(self, text: str, color: str):
        if getattr(self, "status", None) is not None:
            self.status.set_static(text, color)

    # ---- stream handlers --------------------------------------------------
    def _on_conversation(self, cid: int):
        if self._busy:
            self.state.active_conv_id = cid

    def _on_tool(self, name: str):
        if self._busy and getattr(self, "tool_label", None) is not None:
            self.tool_label.setText(f"◆ used {name}")
            self.tool_label.show()

    def _on_chunk(self, text: str):
        if not self._busy:
            return
        self._wake.stop()
        self._status("◇ ANSWER · GENERATED LOCALLY", T.INFO)
        self._acc += text
        self.answer.setText(self._acc)
        vsb = self.panel_scroll.verticalScrollBar()
        vsb.setValue(vsb.maximum())

    def _on_done(self):
        if not self._busy:
            return
        self._busy = False
        self._wake.stop()
        if not self._acc:
            self.answer.setText("(no answer)")
        self.foot.setText(f"answered on-device · {T.MODEL_NAME} · "
                          "0 tokens sent externally")
        if self.state.active_conv_id is not None:
            self.open_chat.show()

    def _on_error(self, msg: str):
        if not self._busy:
            return
        self._busy = False
        self._wake.stop()
        if not self.panel.isVisible():
            self._open_panel("")
        self._status("⚠ ERROR", T.WARN)
        self.answer.setText(msg)
