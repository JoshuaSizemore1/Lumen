"""Quick-launcher palette, which streams real answers
from the daemon. Hotkey-only since new-features item 1: the palette lives in the
frameless hotkey overlay (app.py) — there is no launcher tab."""
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QFrame, QLabel, QLineEdit, QWidget

from .. import sample_data as S
from .. import theme as T
from ..state import AppState
from ..widgets import (
    Chip, ClickLabel, ClickRow, TypingDots, clear_layout, font, hbox, hline,
    label, scroll, vbox,
)

WAKE_THRESHOLD_MS = 1500

# The mock hardcodes #7aa2f7 (not var(--ac)) for hint keys — they stay blue
# under any accent.
_MOCK_BLUE = "#7aa2f7"


def _hint(key: str, text: str) -> QLabel:
    w = QLabel(f'<span style="color:{_MOCK_BLUE}">{key}</span> {text}')
    w.setFont(font(10))
    w.setStyleSheet(f"color: {T.TEXT_DIM}; background: transparent;")
    return w


class LauncherPalette(QFrame):
    """620px command palette: input + streamed response, empty-state hints."""

    def __init__(self, state: AppState, chat_client):
        super().__init__()
        self.state = state
        self.chat = chat_client
        self._busy = False
        self._acc = ""
        # The thread id is app-wide state (state.active_conv_id) so every chat
        # surface continues the same conversation; this only tracks which
        # thread the shell currently RENDERS, to reset stale turns when the
        # active chat changed elsewhere (New chat / sidebar click).
        self._rendered_conv = None
        self.resp_text = None      # current assistant bubble (None until first turn)

        self.setProperty("cls", "palette")
        self.setFixedWidth(620)
        pv = vbox(self, (0, 0, 0, 0), 0)

        head = QFrame()
        head.setProperty("cls", "bar-top")
        hv = hbox(head, (12, 8, 12, 8), 0)
        hv.addWidget(label("QUICK-LAUNCHER", 10, T.TEXT_FAINT, ls=0.4))
        hv.addStretch(1)
        hv.addWidget(label("local · on-device", 10, T.TEXT_FAINT))
        pv.addWidget(head)

        inrow = hbox(m=(18, 16, 18, 16), s=11)
        inrow.addWidget(label("❯", 18, T.ACCENT, 700))
        self.input = QLineEdit()
        self.input.setProperty("cls", "bare")
        self.input.setPlaceholderText("Ask Lumen or type a command…")
        f = self.input.font()
        f.setPixelSize(16)
        self.input.setFont(f)
        self.input.returnPressed.connect(self._submit)
        inrow.addWidget(self.input, 1)
        inrow.addWidget(Chip("llm", T.TEXT_DIM, T.BORDER_STRONG, px=10, radius=4, hpad=7, vpad=2))
        pv.addLayout(inrow)

        self.body = QWidget()
        self.body_lay = vbox(self.body, (0, 0, 0, 0), 0)
        pv.addWidget(self.body)

        foot = QFrame()
        foot.setProperty("cls", "bar-bottom")
        fv = hbox(foot, (18, 9, 18, 9), 16)
        fv.addWidget(_hint("↵", "run"))
        fv.addWidget(_hint("↑↓", "navigate"))
        fv.addWidget(_hint("esc", "dismiss"))
        fv.addStretch(1)
        fv.addWidget(label("super+space to summon", 10, T.TEXT_FAINT))
        pv.addWidget(foot)

        if self.chat is not None:      # sample mode (screenshots) has no daemon
            self.chat.chunk.connect(self._on_chunk)
            self.chat.done.connect(self._on_done)
            self.chat.error.connect(self._on_error)
            self.chat.tool_used.connect(self._on_tool)
            self.chat.conversation.connect(self._on_conversation)
            self.chat.captured.connect(self._on_captured)

        self._wake = QTimer(self)
        self._wake.setSingleShot(True)
        self._wake.setInterval(WAKE_THRESHOLD_MS)
        self._wake.timeout.connect(
            lambda: self._eyebrow("◇ waking model… (cold start, a few seconds)", T.WARN))

        self._show_hints()

    def focus_input(self):
        self.input.setFocus()

    # ---- empty state -----------------------------------------------------
    def _show_hints(self):
        clear_layout(self.body_lay)
        self.body_lay.addWidget(hline(T.BORDER_SOFT))
        w = QWidget()
        v = vbox(w, (18, 12, 18, 6), 0)
        v.addWidget(label("RECENT", 10, T.TEXT_FAINT, ls=1))
        v.addSpacing(6)
        for r in S.RECENTS:
            row = hbox(m=(0, 6, 0, 6), s=11)
            g = label(r["glyph"], 13, T.TEXT_DIM)
            g.setFixedWidth(20)
            g.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row.addWidget(g)
            row.addWidget(label(r["text"], 13, T.TEXT_SECONDARY), 1)
            row.addWidget(label(r["meta"], 11, T.TEXT_FAINT))
            v.addLayout(row)
        v.addSpacing(12)
        v.addWidget(label("TRY", 10, T.TEXT_FAINT, ls=1))
        v.addSpacing(4)
        for t in S.TRIES:
            row = ClickRow(lambda t=t: self._run(t))
            rl = hbox(row, (0, 6, 0, 6), 11)
            g = label("❯", 13, T.ACCENT)
            g.setFixedWidth(20)
            g.setAlignment(Qt.AlignmentFlag.AlignCenter)
            rl.addWidget(g)
            rl.addWidget(label(t, 13, T.TEXT_PRIMARY), 1)
            rl.addWidget(Chip("↵", T.TEXT_FAINT, T.BORDER_STRONG, px=10))
            v.addWidget(row)
        self.body_lay.addWidget(w)

    # ---- conversation (grows in place, like a mini chat) ----------------
    def _show_conversation(self):
        """Replace the empty-state hints with a scrolling thread + a live footer.
        Built once on the first submit; later turns append to it."""
        clear_layout(self.body_lay)
        self.body_lay.addWidget(hline(T.BORDER_SOFT))
        self.thread = QWidget()
        self.thread_lay = vbox(self.thread, (18, 14, 18, 10), 12)
        # Trailing stretch keeps turns packed to the top instead of spread
        # across the viewport; turns insert before it.
        self.thread_lay.addStretch(1)
        self._thread_scroll = scroll(self.thread)
        self._thread_scroll.setMaximumHeight(380)
        vsb = self._thread_scroll.verticalScrollBar()
        vsb.rangeChanged.connect(
            lambda _lo, hi: self._busy and vsb.setValue(hi))
        self.body_lay.addWidget(self._thread_scroll)

        foot = QWidget()
        fv = hbox(foot, (18, 8, 18, 10), 10)
        # Animated while the model works; _eyebrow() freezes it to a fixed
        # status (waking / answer / captured / error) as those states arrive.
        self.resp_eyebrow = TypingDots("◇ thinking", 11, T.INFO)
        fv.addWidget(self.resp_eyebrow)
        fv.addStretch(1)
        self.open_chat_link = ClickLabel("open in Chat ↗", 10, T.ACCENT,
                                         on_click=self._open_in_chat)
        self.open_chat_link.hide()
        fv.addWidget(self.open_chat_link)
        self.resp_foot = label("", 10, T.TEXT_FAINT)
        fv.addWidget(self.resp_foot)
        self.body_lay.addWidget(foot)

    def _add_user_turn(self, text: str):
        row = hbox(s=8)
        row.addWidget(label("you", 10, T.TEXT_FAINT))
        row.addWidget(label(text, 13, T.TEXT_SECONDARY, sans=True, wrap=True), 1)
        self.thread_lay.insertLayout(self.thread_lay.count() - 1, row)

    def _begin_assistant_turn(self):
        self._acc = ""
        self.tool_lab = label("", 10, T.TEXT_FAINT)
        self.tool_lab.hide()
        self.thread_lay.insertWidget(self.thread_lay.count() - 1, self.tool_lab)
        self.resp_text = label("", 14, T.TEXT_PRIMARY, sans=True, wrap=True)
        self.thread_lay.insertWidget(self.thread_lay.count() - 1, self.resp_text)
        self.resp_eyebrow.start("◇ thinking")   # animate until the answer lands

    def _eyebrow(self, text: str, color: str):
        if getattr(self, "resp_eyebrow", None) is not None:
            self.resp_eyebrow.set_static(text, color)

    def _run(self, text: str):
        self.input.setText(text)
        self._submit()

    def _submit(self):
        msg = self.input.text().strip()
        if self.chat is None or not msg or self._busy:
            return
        self._busy = True
        active = self.state.active_conv_id
        if self.resp_text is None or self._rendered_conv != active:
            # first turn, or the active chat changed elsewhere (New chat /
            # sidebar click): rebuild the shell so threads never mix visually
            self._show_conversation()
        self._rendered_conv = active
        self._add_user_turn(msg)
        self._begin_assistant_turn()
        self.input.clear()
        self._wake.start()
        payload = {"message": msg}
        if active is not None:          # continue the one active thread
            payload["conversation_id"] = active
        else:
            # fresh thread's first turn only: note-shaped text may become a
            # todo (quick capture); once a conversation is going, you're chatting
            payload["capture_ok"] = True
        self.chat.send("chat", payload)

    def _open_in_chat(self):
        if self.state.active_conv_id is not None:
            self.state.open_chat_requested.emit(self.state.active_conv_id)

    def _on_conversation(self, cid: int):
        if self._busy:                  # only claim the id for the turn we launched
            self.state.active_conv_id = cid
            self._rendered_conv = cid

    def _on_captured(self, todo: dict):
        """Quick capture landed: the turn's answer is a toast, not prose."""
        if not self._busy:
            return
        self._wake.stop()
        self._eyebrow("✓ captured", T.OK)
        extra = f" · due {todo['due_date']}" if todo.get("due_date") else ""
        tags = f" [{', '.join(todo['tags'])}]" if todo.get("tags") else ""
        self._acc = f"✓ Added todo: {todo['text']}{extra}{tags}"
        self.resp_text.setText(self._acc)
        self._undo_lab = ClickLabel("Undo", 11, T.ACCENT,
                                    on_click=lambda tid=todo["id"]: self._undo_capture(tid))
        self.thread_lay.insertWidget(self.thread_lay.count() - 1, self._undo_lab)
        self.state.refresh_todos()      # the Todos screen shows it immediately

    def _undo_capture(self, tid):
        self.state.delete_todo(tid)
        self._undo_lab.setText("removed")
        self._undo_lab._on_click = None

    def _on_tool(self, name: str):
        if self._busy and getattr(self, "tool_lab", None) is not None:
            self.tool_lab.setText(f"🔧 used {name}")
            self.tool_lab.show()

    def _on_chunk(self, text: str):
        if not self._busy:
            return
        self._wake.stop()
        self._eyebrow("◇ answer · generated locally", T.INFO)
        self._acc += text
        self.resp_text.setText(self._acc)

    def _on_done(self):
        if not self._busy:
            return
        self._busy = False
        self._wake.stop()
        if not self._acc:
            self.resp_text.setText("(no answer)")
        self.resp_foot.setText(f"answered on-device · {T.MODEL_NAME}")
        if self.state.active_conv_id is not None:
            self.open_chat_link.show()

    def _on_error(self, msg: str):
        if not self._busy:
            return
        self._busy = False
        self._wake.stop()
        if self.resp_text is None:
            self._show_conversation()
            self._begin_assistant_turn()
        self._eyebrow("⚠ error", T.WARN)
        self.resp_text.setText(msg)

