"""Mail-rule editor: a label plus any-of matchers.

Save hands the rule to rules.create/update — creation still passes the daemon's
confirm gate (carrying the apply-to-existing checkbox), so this card never
writes to Gmail itself.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QLineEdit, QWidget

from . import theme as T
from .overlays import _Card, _Scrim
from .widgets import (
    ClickChip, ClickLabel, FlowLayout, button, clear_layout, eyebrow, font,
    hbox, hline, label, scroll, shadow, vbox,
)

RULE_W = 480


def _split(text: str) -> list[str]:
    return [t.strip() for t in text.split(",") if t.strip()]


class RuleDialog(_Scrim):
    def __init__(self, parent, state):
        super().__init__(parent)
        self.state = state
        self._rid = None
        self._on_dismiss = self.hide

        self.card = _Card()
        self.card.setProperty("role", "dialog")
        self.card.setFixedWidth(T.sc(RULE_W))
        shadow(self.card, 60, 24, 76)
        self.card_lay = vbox(self.card, (0, 0, 0, 0), 0)
        self.set_card(self.card)

    def open(self, prefill: dict | None = None):
        p = prefill or {}
        self._rid = p.get("id")
        self._build(p)
        self.pop()
        self.label_edit.setFocus()

    def _field(self, parent_lay, caption: str, value: str,
               placeholder: str) -> QLineEdit:
        parent_lay.addWidget(eyebrow(caption, px=9.5, ls=1))
        edit = QLineEdit(value)
        edit.setPlaceholderText(placeholder)
        edit.setFont(font(13.5))
        parent_lay.addWidget(edit)
        return edit

    def _build(self, p: dict):
        clear_layout(self.card_lay)

        head = hbox(m=(20, 15, 20, 15), s=10)
        head.addWidget(label("Edit mail rule" if self._rid else "New mail rule",
                             15, T.TEXT_PRIMARY, 600))
        head.addStretch(1)
        head.addWidget(label("APPLIED ON SYNC", 9.5, T.TEXT_FAINT, mono=True,
                             ls=1))
        self.card_lay.addLayout(head)
        self.card_lay.addWidget(hline(T.BORDER_MED))

        body = vbox(m=(20, 14, 20, 14), s=9)
        body.addWidget(label(
            "Matching mail gets the label and leaves the inbox — any one "
            "condition is enough.", 13, T.TEXT_SECONDARY, wrap=True))
        body.addSpacing(4)

        self.label_edit = self._field(body, "Label", p.get("label", ""),
                                      "e.g. Bills")
        if self.state.mail_labels:
            chips_host = QWidget()
            chips = FlowLayout(chips_host)
            for name in self.state.mail_labels:
                c = T.label_color(name)
                chips.addWidget(ClickChip(
                    name, c, c, px=9,
                    on_click=lambda n=name: self.label_edit.setText(n)))
            body.addWidget(chips_host)

        self.from_edit = self._field(body, "From (comma-separated)",
                                     ", ".join(p.get("from_addrs", [])),
                                     "sender@example.com")
        self.domain_edit = self._field(body, "Domains",
                                       ", ".join(p.get("domains", [])),
                                       "example.com")
        self.subj_edit = self._field(body, "Subject keywords",
                                     ", ".join(p.get("subject_kw", [])),
                                     "invoice, statement")
        self.body_edit = self._field(body, "Body keywords",
                                     ", ".join(p.get("body_kw", [])), "")
        self.hint = label("", 11.5, T.WARN)
        body.addWidget(self.hint)
        self.card_lay.addLayout(body)

        foot = hbox(m=(20, 4, 20, 16), s=11)
        if self._rid is not None:
            foot.addWidget(ClickLabel(
                "delete rule", 11, T.TEXT_FAINT, mono=True,
                on_click=self._delete))
        foot.addStretch(1)
        cancel = button("Cancel", "ghost", px=13.5, height=36)
        cancel.clicked.connect(self.hide)
        foot.addWidget(cancel)
        save = button("Save rule", "primary", px=13.5, height=36)
        save.clicked.connect(self._save)
        foot.addWidget(save)
        self.card_lay.addLayout(foot)

    def _save(self):
        rule = {"label": self.label_edit.text().strip(),
                "from_addrs": _split(self.from_edit.text()),
                "domains": _split(self.domain_edit.text()),
                "subject_kw": _split(self.subj_edit.text()),
                "body_kw": _split(self.body_edit.text())}
        if not rule["label"] or not any(
                rule[c] for c in ("from_addrs", "domains", "subject_kw",
                                  "body_kw")):
            self.hint.setText("Needs a label and at least one condition.")
            return

        def done(r):
            self.state.toast_requested.emit(
                ("✓ " if (r or {}).get("ok") else "")
                + ((r or {}).get("message") or "Saved."))
            # A save may have backfill-labeled mail — reload rows and chips.
            self.state.refresh_mails()

        if self._rid is None:
            self.state.create_rule(rule, done)
        else:
            self.state.update_rule(self._rid, rule, done)
        self.hide()

    def _delete(self):
        def done(r):
            self.state.toast_requested.emit(
                ("✓ " if (r or {}).get("ok") else "")
                + ((r or {}).get("message") or "Rule deleted."))
            self.state.refresh_mails()
        self.state.delete_rule(self._rid, done)
        self.hide()
