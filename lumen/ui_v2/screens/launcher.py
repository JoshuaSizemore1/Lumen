"""Quick-launcher: faux desktop canvas + the palette, which streams real answers
from the daemon. The palette is shared by the full tab screen and the frameless
hotkey overlay."""
from PyQt6.QtCore import Qt, QPointF, QTimer
from PyQt6.QtGui import QPainter, QRadialGradient
from PyQt6.QtWidgets import QFrame, QLabel, QLineEdit, QWidget

from .. import sample_data as S
from .. import theme as T
from ..state import AppState
from ..widgets import (
    Chip, ClickRow, clear_layout, font, hbox, hline, label, qcolor, scroll, vbox,
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

    # ---- streaming response ---------------------------------------------
    def _show_response_shell(self):
        clear_layout(self.body_lay)
        self.body_lay.addWidget(hline(T.BORDER_SOFT))
        w = QWidget()
        v = vbox(w, (18, 14, 18, 10), 0)
        self.resp_eyebrow = label("◇ thinking…", 11, T.INFO)
        v.addWidget(self.resp_eyebrow)
        self.tool_lab = label("", 10, T.TEXT_FAINT)
        self.tool_lab.hide()
        v.addWidget(self.tool_lab)
        v.addSpacing(9)
        self.resp_text = label("", 14, T.TEXT_PRIMARY, sans=True, wrap=True)
        v.addWidget(self.resp_text)
        v.addSpacing(10)
        self.resp_foot = label("", 10, T.TEXT_FAINT)
        v.addWidget(self.resp_foot)
        self.body_lay.addWidget(w)

    def _eyebrow(self, text: str, color: str):
        if getattr(self, "resp_eyebrow", None) is not None:
            self.resp_eyebrow.setText(text)
            pal = self.resp_eyebrow.palette()
            pal.setColor(self.resp_eyebrow.foregroundRole(), qcolor(color))
            self.resp_eyebrow.setPalette(pal)

    def _run(self, text: str):
        self.input.setText(text)
        self._submit()

    def _submit(self):
        msg = self.input.text().strip()
        if self.chat is None or not msg or self._busy:
            return
        self._busy = True
        self._acc = ""
        self._show_response_shell()
        self._wake.start()
        self.chat.send("chat", {"message": msg})

    def _on_tool(self, name: str):
        if getattr(self, "tool_lab", None) is not None:
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

    def _on_error(self, msg: str):
        self._busy = False
        self._wake.stop()
        if getattr(self, "resp_eyebrow", None) is None:
            self._show_response_shell()
        self._eyebrow("⚠ error", T.WARN)
        self.resp_text.setText(msg)


class LauncherScreen(QWidget):
    """Full tab: faux desktop with the palette centered on it."""

    def __init__(self, state: AppState, chat_client=None):
        super().__init__()
        self.state = state
        chat = chat_client or state._chat

        root = vbox(self)
        content = QWidget()
        cv = vbox(content, (0, 0, 0, 0), 0)

        waybar = QFrame()
        waybar.setObjectName("waybar")
        waybar.setFixedHeight(T.WAYBAR_H)
        wb = hbox(waybar, (12, 0, 12, 0), 0)
        ws = hbox(s=10)
        ws.addWidget(label("1", 10, T.ACCENT))
        for n in "234":
            ws.addWidget(label(n, 10, T.TEXT_DIM))
        wb.addLayout(ws)
        wb.addStretch(1)
        rs = hbox(s=14)
        for t in (T.MODEL_NAME, "local", "62%"):
            rs.addWidget(label(t, 10, T.TEXT_DIM))
        wb.addLayout(rs)
        cv.addWidget(waybar)

        cv.addSpacing(78)
        row = hbox(s=0)
        row.addStretch(1)
        row.addWidget(LauncherPalette(state, chat))
        row.addStretch(1)
        cv.addLayout(row)

        cap = label("centered overlay · summoned by global hotkey, dismisses on esc or focus loss",
                    10, T.TEXT_GHOST)
        cap.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        capw = QWidget()
        cl = vbox(capw, (0, 26, 0, 40), 0)
        cl.addWidget(cap)
        cv.addWidget(capw)
        cv.addStretch(1)

        root.addWidget(scroll(content), 1)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), qcolor(T.BG_OVERLAY))
        w, h = self.width(), self.height()
        for cx, cy, col, stop in ((0.20, 0.12, "#181a26", 0.38), (0.82, 0.78, "#161824", 0.40)):
            fx = max(cx, 1 - cx) * w
            fy = max(cy, 1 - cy) * h
            radius = (fx * fx + fy * fy) ** 0.5 * stop
            g = QRadialGradient(QPointF(cx * w, cy * h), radius)
            g.setColorAt(0.0, qcolor(col))
            g.setColorAt(1.0, qcolor(col, 0))
            p.fillRect(self.rect(), g)
