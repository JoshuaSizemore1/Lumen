"""Mail: two-pane inbox skeleton. Real sync is Phase 6; Reply demonstrates the
write-confirmation flow with placeholder content."""

from PyQt6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from lumen.ui import theme
from lumen.ui.confirm_dialog import ConfirmDialog
from lumen.ui.widgets import button, label

MESSAGES = [
    ("GitHub", "08:12", "PR #142: swap to local model runtime", False),
    ("Priya Nair", "07:40", "Re: sync interval defaults", True),
    ("Dr. Okafor's office", "Tue", "Appointment reminder — Jul 9", False),
    ("Sarah Chen", "Mon", "Book club: next pick?", False),
    ("Linux Weekly", "Wed", "Wayland color management lands", False),
]
BODY = """Thanks for writing this up.

15 minutes as the default feels right — long enough to stay quiet, short enough \
that the tray never goes stale. Can we expose it under [sync] so power users can \
drop it to 5?

— Priya"""


class MailScreen(QWidget):
    def __init__(self):
        super().__init__()
        split = QHBoxLayout(self)
        split.setContentsMargins(0, 0, 0, 0)
        split.setSpacing(0)

        # message list
        lst = QVBoxLayout()
        lst.setContentsMargins(16, 16, 16, 16)
        head = QHBoxLayout()
        head.addWidget(label("Inbox", "h2"))
        head.addWidget(label("4 unread", "dim"))
        head.addStretch()
        lst.addLayout(head)
        for sender, when, subj, selected in MESSAGES:
            row = QFrame()
            if selected:
                row.setStyleSheet(
                    f"background: {theme.ACCENT_SOFT}; border-left: 2px solid {theme.ACCENT};")
            rv = QVBoxLayout(row)
            rv.setContentsMargins(10, 8, 10, 8)
            rv.setSpacing(1)
            top = QHBoxLayout()
            top.addWidget(label(sender, "secondary"))
            top.addStretch()
            top.addWidget(label(when, "faint"))
            rv.addLayout(top)
            rv.addWidget(label(subj, "muted"))
            lst.addWidget(row)
        lst.addStretch()
        holder = QWidget()
        holder.setLayout(lst)
        holder.setFixedWidth(334)
        split.addWidget(holder)

        # reading pane
        pane = QVBoxLayout()
        pane.setContentsMargins(26, 20, 26, 20)
        pane.addWidget(label("Re: sync interval defaults", "h2"))
        meta = QHBoxLayout()
        meta.addWidget(label("Priya Nair", "secondary"))
        meta.addWidget(label("to me · Thu · 07:40", "dim"))
        meta.addStretch()
        self.reply_btn = button("↳ Reply", "primary")
        self.reply_btn.clicked.connect(self._reply)
        meta.addWidget(self.reply_btn)
        meta.addWidget(button("Archive", "ghost"))
        pane.addLayout(meta)
        pane.addWidget(label(BODY, "sans", wrap=True))
        pane.addStretch()
        pane_holder = QWidget()
        pane_holder.setLayout(pane)
        split.addWidget(pane_holder, 1)

    def _reply(self) -> None:
        ConfirmDialog.ask(
            "Send email",
            "Lumen will send this message from your connected Gmail account.",
            [("To", "priya.nair@company.com"),
             ("Subject", "Re: sync interval defaults"),
             ("Body", "(placeholder — email send lands in Phase 7)")],
            "Send email", parent=self)
