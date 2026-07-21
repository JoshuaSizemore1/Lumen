"""Quick launch (Super+L).

Built from the mockup's command palette, which the mock only ever reached via a
sidebar "Quick launch" row. Per the design call that row is gone: the palette is
hotkey-summoned, which is what it was always shaped like — 560px, command list,
type-to-filter, ↵ runs the top hit.

Anything typed that isn't a command falls through to Lumen as a question,
streamed inline, continuing the same conversation as the Chat screen and the
ask bar.
"""
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QDialog, QFrame, QLineEdit, QWidget

from . import theme as T
from .components import accent_fill
from .widgets import (
    Chip, ClickRow, TypingDots, clear_layout, font, hbox, hline, label, scroll,
    shadow, vbox,
)

WAKE_THRESHOLD_MS = 1500

# (icon, label, hint, kind, target)
COMMANDS = (
    ("◇", "Go to Today", "1", "view", "today"),
    ("▦", "Open Calendar", "2", "view", "calendar"),
    ("✉", "Open Mail", "3", "view", "mail"),
    ("☑", "Open Todos", "4", "view", "todos"),
    ("❡", "Open Books", "5", "view", "books"),
    ("␣", "Open Chat", "6", "view", "chat"),
    ("▤", "Browse Files", "7", "view", "files"),
    ("✎", "Compose email…", "write", "compose", None),
    ("▲", "New calendar event…", "write", "event", None),
    ("⚙", "Open Settings", "⌘,", "view", "settings"),
)


class LauncherPalette(QFrame):
    """The palette body. Lives inside the frameless overlay below."""

    def __init__(self, state, chat_client):
        super().__init__()
        self.setProperty("role", "dialog")
        self.setFixedWidth(T.LAUNCHER_W)
        self.state = state
        self.chat = chat_client
        self._busy = False
        self._acc = ""
        self._filtered = list(COMMANDS)
        self.status = None

        v = vbox(self, (0, 0, 0, 0), 0)

        head = hbox(m=(18, 15, 18, 15), s=12)
        head.addWidget(label("❯", 13, T.ACCENT, mono=True))
        self.input = QLineEdit()
        self.input.setProperty("cls", "bare")
        self.input.setPlaceholderText("Type a command…")
        self.input.setFont(font(15))
        self.input.textChanged.connect(self._filter)
        self.input.returnPressed.connect(self._activate)
        head.addWidget(self.input, 1)
        head.addWidget(Chip("ON-DEVICE", T.TEXT_FAINT, T.BORDER_STRONG, px=9,
                            hpad=7, vpad=2, ls=1))
        v.addLayout(head)
        v.addWidget(hline(T.BORDER_MED))

        self.body = QWidget()
        self.body_lay = vbox(self.body, (8, 7, 8, 7), 0)
        self.body_scroll = scroll(self.body)
        self.body_scroll.setMaximumHeight(320)
        v.addWidget(self.body_scroll)

        foot = QFrame()
        foot.setProperty("role", "dialogfoot")
        fv = hbox(foot, (18, 9, 18, 9), 16)
        for text in ("↵ run", "esc dismiss"):
            fv.addWidget(label(text, 10, T.TEXT_FAINT, mono=True))
        fv.addStretch(1)
        fv.addWidget(label("super+L to summon", 10, T.TEXT_FAINT, mono=True))
        v.addWidget(foot)

        if self.chat is not None:
            self.chat.chunk.connect(self._on_chunk)
            self.chat.done.connect(self._on_done)
            self.chat.error.connect(self._on_error)
            self.chat.captured.connect(self._on_captured)
            self.chat.conversation.connect(self._on_conversation)

        self._wake = QTimer(self)
        self._wake.setSingleShot(True)
        self._wake.setInterval(WAKE_THRESHOLD_MS)
        self._wake.timeout.connect(
            lambda: self._set_status("◇ waking model… (cold start)", T.WARN))

        self._render_commands()

    def reset(self):
        self.input.clear()
        self._busy = False
        self._acc = ""
        self._render_commands()

    def focus_input(self):
        self.input.setFocus()
        self.input.selectAll()

    # ---- command list -----------------------------------------------------
    def _filter(self, text: str):
        q = text.lower().strip()
        self._filtered = [c for c in COMMANDS if q in c[1].lower()] if q \
            else list(COMMANDS)
        self._render_commands()

    def _render_commands(self):
        clear_layout(self.body_lay)
        if not self._filtered:
            self.body_lay.addWidget(label(
                "No matching command — ↵ asks Lumen instead", 13, T.TEXT_FAINT,
                wrap=True))
            self.body_lay.addStretch(1)
            return
        for i, cmd in enumerate(self._filtered):
            self.body_lay.addWidget(self._row(cmd, first=(i == 0)))
        self.body_lay.addStretch(1)

    def _row(self, cmd, first: bool) -> QWidget:
        icon, text, hint, kind, target = cmd
        row = ClickRow(lambda c=cmd: self._run(c))
        lay = hbox(row, (11, 9, 11, 9), 11)
        ic = label(icon, 12, T.ACCENT if kind != "view" else T.TEXT_FAINT,
                   mono=True)
        ic.setFixedWidth(T.sc(22))
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(ic)
        lay.addWidget(label(text, 14, T.TEXT_PRIMARY), 1)
        lay.addWidget(Chip(hint, T.TEXT_FAINT, T.BORDER_MED, px=10, hpad=6,
                           vpad=1))
        if first:
            row.setStyleSheet(
                f"ClickRow {{ background: {accent_fill()}; border-radius: 6px; }}")
        return row

    def _activate(self):
        """↵: run the top command, or ask Lumen when nothing matches."""
        if self._filtered:
            self._run(self._filtered[0])
        else:
            self._ask(self.input.text().strip())

    def _run(self, cmd):
        _icon, _text, _hint, kind, target = cmd
        if kind == "view":
            self.state.view_requested.emit(target)
            self.window().hide()
        elif kind == "compose":
            self.state.open_compose({})
            self.window().hide()
        elif kind == "event":
            self.state.event_compose_requested.emit({})
            self.window().hide()

    # ---- ask fallthrough --------------------------------------------------
    def _ask(self, msg: str):
        if not msg or self._busy or self.chat is None:
            return
        self._busy = True
        self._acc = ""
        self.input.clear()
        clear_layout(self.body_lay)

        self.status = TypingDots("◇ thinking", 10, T.INFO, ls=1.5)
        self.body_lay.addWidget(self.status)
        self.body_lay.addSpacing(8)
        self.body_lay.addWidget(label(msg, 12, T.TEXT_MUTED, wrap=True))
        self.body_lay.addSpacing(8)
        self.answer = label("", 14, T.TEXT_PRIMARY, wrap=True)
        self.body_lay.addWidget(self.answer)
        self.body_lay.addStretch(1)
        self.status.start("◇ thinking")
        self._wake.start()

        payload = {"message": msg}
        if self.state.active_conv_id is not None:
            payload["conversation_id"] = self.state.active_conv_id
        else:
            payload["capture_ok"] = True
        self.chat.send("chat", payload)

    def _set_status(self, text: str, color: str):
        if self.status is not None:
            self.status.set_static(text, color)

    def _on_conversation(self, cid: int):
        if self._busy:
            self.state.active_conv_id = cid

    def _on_captured(self, todo: dict):
        if not self._busy:
            return
        self._wake.stop()
        self._set_status("✓ CAPTURED", T.OK)
        extra = f" · due {todo['due_date']}" if todo.get("due_date") else ""
        self.answer.setText(f"✓ Added todo: {todo['text']}{extra}")
        self.state.refresh_todos()

    def _on_chunk(self, text: str):
        if not self._busy:
            return
        self._wake.stop()
        self._set_status("◇ ANSWER · GENERATED LOCALLY", T.INFO)
        self._acc += text
        self.answer.setText(self._acc)

    def _on_done(self):
        if not self._busy:
            return
        self._busy = False
        self._wake.stop()
        if not self._acc:
            self.answer.setText("(no answer)")

    def _on_error(self, msg: str):
        if not self._busy:
            return
        self._busy = False
        self._wake.stop()
        self._set_status("⚠ ERROR", T.WARN)
        self.answer.setText(msg)


class LauncherOverlay(QDialog):
    """Frameless hotkey-summoned window wrapping the palette."""

    def __init__(self, state, chat_client):
        super().__init__()
        self.state = state
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(T.LAUNCHER_W + 2)
        v = vbox(self, (1, 1, 1, 1), 0)
        self.palette_widget = LauncherPalette(state, chat_client)
        shadow(self.palette_widget, 90, 34, 90)
        v.addWidget(self.palette_widget)

    def toggle(self):
        if self.isVisible():
            self.hide()
        else:
            self.state.warm_model()     # load while the user is still typing
            self.palette_widget.reset()
            self.show()
            self.raise_()
            self.activateWindow()
            self.palette_widget.focus_input()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(ev)

    def event(self, ev):
        if ev.type() == ev.Type.WindowDeactivate:
            self.hide()
        return super().event(ev)
