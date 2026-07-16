"""Mail-rule editor overlay: label + any-of matchers. Save hands the rule to
rules.create/update — creation still passes the daemon's confirm gate (with
the apply-to-existing checkbox), so this popup never writes to Gmail itself."""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QFrame, QLineEdit, QWidget

from . import theme as T
from .widgets import (ClickChip, FlowLayout, button, clear_layout, hbox,
                      hline, label, qcolor, vbox)


def _split(text: str) -> list[str]:
    return [t.strip() for t in text.split(",") if t.strip()]


class RuleDialog(QWidget):
    """Dimmed overlay with a centered 480px editor card."""

    def __init__(self, parent: QWidget, state):
        super().__init__(parent)
        self.state = state
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._rid = None

        lay = hbox(self, (20, 20, 20, 20), 0)
        lay.addStretch(1)
        wrap = vbox(s=0)
        wrap.addStretch(1)
        self.card = QFrame()
        self.card.setProperty("cls", "dialog")
        self.card.setFixedWidth(480)
        self.card_lay = vbox(self.card, (18, 16, 18, 16), 8)
        wrap.addWidget(self.card)
        wrap.addStretch(1)
        lay.addLayout(wrap)
        lay.addStretch(1)
        self.hide()

    def open(self, prefill: dict | None = None):
        p = prefill or {}
        self._rid = p.get("id")
        self._build(p)
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.label_edit.setFocus()

    def _field(self, caption: str, value: str, placeholder: str) -> QLineEdit:
        self.card_lay.addWidget(label(caption, 11, T.TEXT_DIM))
        edit = QLineEdit(value)
        edit.setPlaceholderText(placeholder)
        f = edit.font()
        f.setPixelSize(12)
        edit.setFont(f)
        self.card_lay.addWidget(edit)
        return edit

    def _build(self, p: dict):
        clear_layout(self.card_lay)
        self.card_lay.addWidget(label(
            "Edit mail rule" if self._rid else "New mail rule",
            14, T.TEXT_PRIMARY, 600))
        self.card_lay.addWidget(label(
            "Matching mail gets the label and leaves the inbox — any "
            "condition is enough.", 11, T.TEXT_DIM, wrap=True))
        self.card_lay.addWidget(hline(T.BORDER_MED))
        self.label_edit = self._field("label", p.get("label", ""), "e.g. Bills")
        if self.state.mail_labels:
            chips_host = QWidget()
            chips = FlowLayout(chips_host)
            for name in self.state.mail_labels:
                c = T.label_color(name)
                chips.addWidget(ClickChip(
                    name, c, c, px=9,
                    on_click=lambda n=name: self.label_edit.setText(n)))
            self.card_lay.addWidget(chips_host)
        self.from_edit = self._field("from (comma-separated)",
                                     ", ".join(p.get("from_addrs", [])),
                                     "sender@example.com")
        self.domain_edit = self._field("domains",
                                       ", ".join(p.get("domains", [])),
                                       "example.com")
        self.subj_edit = self._field("subject keywords",
                                     ", ".join(p.get("subject_kw", [])),
                                     "invoice, statement")
        self.body_edit = self._field("body keywords",
                                     ", ".join(p.get("body_kw", [])), "")
        self.hint = label("", 11, T.WARN)
        self.card_lay.addWidget(self.hint)
        foot = hbox(m=(0, 8, 0, 0), s=10)
        cancel = button("Cancel", "cancel", 12, 34)
        cancel.clicked.connect(self.hide)
        foot.addWidget(cancel, 1)
        save = button("Save rule", "confirm", 12, 34)
        save.clicked.connect(self._save)
        foot.addWidget(save, 1)
        self.card_lay.addLayout(foot)

    def _save(self):
        rule = {"label": self.label_edit.text().strip(),
                "from_addrs": _split(self.from_edit.text()),
                "domains": _split(self.domain_edit.text()),
                "subject_kw": _split(self.subj_edit.text()),
                "body_kw": _split(self.body_edit.text())}
        if not rule["label"] or not any(
                rule[c] for c in ("from_addrs", "domains",
                                  "subject_kw", "body_kw")):
            self.hint.setText("needs a label and at least one condition")
            return
        done = lambda r: self.state.toast_requested.emit(
            ("✓ " if r.get("ok") else "") + (r.get("message") or "Saved."))
        if self._rid is None:
            self.state.create_rule(rule, done)
        else:
            self.state.update_rule(self._rid, rule, done)
        self.hide()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(ev)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), qcolor("#06070b", int(0.72 * 255)))
