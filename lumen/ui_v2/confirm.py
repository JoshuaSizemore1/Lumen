"""Write-action confirmation overlay + success toast (in-window, like the mock)."""
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QFrame, QLabel, QWidget

from . import theme as T
from .widgets import (
    CompositeButton, clear_layout, font, hbox, hline, label, qcolor, vbox,
)


class ConfirmOverlay(QWidget):
    """Dimmed overlay covering the window with a centered 480px dialog card."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._payload: dict = {}
        self._on_done = None

        lay = hbox(self, (20, 20, 20, 20), 0)
        lay.addStretch(1)
        card_wrap = vbox(s=0)
        card_wrap.addStretch(1)
        self.card = QFrame()
        self.card.setProperty("cls", "dialog")
        self.card.setFixedWidth(480)
        self.card_lay = vbox(self.card, (0, 0, 0, 0), 0)
        card_wrap.addWidget(self.card)
        card_wrap.addStretch(1)
        lay.addLayout(card_wrap)
        lay.addStretch(1)
        self.hide()

    def open(self, payload: dict, on_done):
        """on_done(approved: bool, payload: dict) fires exactly once per open —
        on confirm, cancel, or esc. For a daemon confirm (payload carries a
        confirm_id) the deny path must fire too, or the daemon blocks until it
        times out."""
        self._payload = payload
        self._on_done = on_done
        self._build()
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.setFocus()

    def _build(self):
        clear_layout(self.card_lay)
        c = self._payload

        head = QWidget()
        hl = hbox(head, (18, 16, 18, 12), 11)
        icon = QLabel(c["icon"])
        icon.setFixedSize(30, 30)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFont(font(15))
        icon.setStyleSheet(f"background: {T.ACCENT_SOFT_QSS}; border: 1px solid {T.ACCENT}; "
                           f"border-radius: 7px; color: {T.ACCENT};")
        hl.addWidget(icon)
        tv = vbox(s=1)
        tv.addWidget(label(c["title"], 14, T.TEXT_PRIMARY, 600))
        tv.addWidget(label("⚠ WRITE ACTION · TOUCHES A CONNECTED ACCOUNT", 10, T.WARN, ls=0.4))
        hl.addLayout(tv, 1)
        self.card_lay.addWidget(head)
        self.card_lay.addWidget(hline(T.BORDER_MED))

        body = QWidget()
        bl = vbox(body, (18, 14, 18, 14), 0)
        bl.addWidget(label(c["intro"], 12, T.TEXT_BODY_MUTED, sans=True, wrap=True))
        bl.addSpacing(12)
        inset = QFrame()
        inset.setProperty("cls", "inset")
        il = vbox(inset, (12, 10, 12, 10), 0)
        for k, v in c["rows"]:
            row = hbox(m=(0, 5, 0, 5), s=12)
            kl = label(k, 11, T.TEXT_DIM)
            kl.setFixedWidth(64)
            kl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
            row.addWidget(kl)
            row.addWidget(label(v, 12, T.TEXT_PRIMARY, wrap=True), 1)
            il.addLayout(row)
        bl.addWidget(inset)
        self.card_lay.addWidget(body)

        foot = QWidget()
        fl = hbox(foot, (18, 12, 18, 16), 10)
        cancel = CompositeButton("cancel", [label("Cancel", 13, T.TEXT_SECONDARY),
                                            label("esc", 10, T.TEXT_DIM)], height=38)
        cancel.clicked.connect(self.dismiss)
        fl.addWidget(cancel, 10)
        ok = CompositeButton("confirm", [label(c["confirm_label"], 13, T.ACCENT_ON, 600),
                                         label("⌘↵", 10, "#0d0e1499")], height=38)
        ok.clicked.connect(self._confirm)
        fl.addWidget(ok, 14)
        self.card_lay.addWidget(foot)

    def _finish(self, approved: bool):
        if self._on_done is None:
            self.hide()
            return
        cb, payload = self._on_done, self._payload
        self._on_done = None          # fire once: guard esc-after-click etc.
        self.hide()
        cb(approved, payload)

    def _confirm(self):
        self._finish(True)

    def dismiss(self):
        self._finish(False)

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self.dismiss()
        elif ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._confirm()
        else:
            super().keyPressEvent(ev)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), qcolor("#06070b", int(0.72 * 255)))


class Toast(QLabel):
    """Bottom-centered success toast, auto-dismisses after 2.6s."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setFont(font(12))
        self.setStyleSheet(f"background: {T.BG_DIALOG}; border: 1px solid #2a3a2a; "
                           f"color: {T.OK}; border-radius: 8px; padding: 9px 16px;")
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self.hide()

    def pop(self, text: str):
        self.setText(text)
        self.adjustSize()
        pw = self.parentWidget()
        self.move((pw.width() - self.width()) // 2, pw.height() - self.height() - 20)
        self.show()
        self.raise_()
        self._timer.start(2600)

    def reposition(self):
        if self.isVisible():
            pw = self.parentWidget()
            self.move((pw.width() - self.width()) // 2, pw.height() - self.height() - 20)
