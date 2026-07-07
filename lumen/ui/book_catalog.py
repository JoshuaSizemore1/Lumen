"""Books: reading log + LLM recommendations skeleton. Real catalog is Phase 4.
Recs panel is deliberately visually distinct (dashed purple) from the log."""

from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from lumen.ui import theme
from lumen.ui.widgets import Panel, button, label

BOOKS = [
    ("The Left Hand of Darkness", "Ursula K. Le Guin", "★★★★★", "finished Jun 28",
     "Slow burn, worth it. Gethen worldbuilding stuck with me."),
    ("Project Hail Mary", "Andy Weir", "★★★★☆", "finished Jun 10",
     "Propulsive. Rocky carries it more than the science."),
    ("The Dispossessed", "Ursula K. Le Guin", "★★★★★", "finished May 22",
     "Ambiguous utopia. Reread candidate."),
    ("Piranesi", "Susanna Clarke", "★★★★☆", "finished Apr 30",
     "Quiet, strange, lingers for weeks."),
]
RECS = [
    ("A Fire Upon the Deep", "Vernor Vinge",
     "↳ Big-idea space opera near Project Hail Mary's energy, but denser."),
    ("The Word for World Is Forest", "Ursula K. Le Guin",
     "↳ You rated two Le Guin novels 5★ — the short Hainish entry you haven't logged."),
    ("Solaris", "Stanisław Lem",
     "↳ Shares Piranesi's uncanny, unknowable-space mood."),
]


class BooksScreen(QWidget):
    def __init__(self):
        super().__init__()
        split = QHBoxLayout(self)
        split.setContentsMargins(26, 22, 26, 22)
        split.setSpacing(24)

        # reading log
        log = QVBoxLayout()
        head = QHBoxLayout()
        head.addWidget(label("Reading log", "h2"))
        head.addWidget(label("4 books · your catalog", "sub"))
        head.addStretch()
        log.addLayout(head)

        form = Panel()
        fv = QVBoxLayout(form)
        fv.setContentsMargins(12, 12, 12, 12)
        fv.addWidget(label("+ ADD ENTRY", "eyebrow"))
        line1 = QHBoxLayout()
        for placeholder, stretch in (("Title", 2), ("Author", 1)):
            f = QLineEdit()
            f.setProperty("kind", "field")
            f.setPlaceholderText(placeholder)
            line1.addWidget(f, stretch)
        line1.addWidget(label("★5", "status"))
        fv.addLayout(line1)
        line2 = QHBoxLayout()
        notes = QLineEdit()
        notes.setProperty("kind", "field")
        notes.setPlaceholderText("Notes (free text)")
        line2.addWidget(notes, 1)
        line2.addWidget(button("Log", "soft"))
        fv.addLayout(line2)
        log.addWidget(form)

        for title, author, stars, fin, note in BOOKS:
            row1 = QHBoxLayout()
            row1.addWidget(label(title, "secondary"))
            row1.addWidget(label(author, "muted"))
            row1.addStretch()
            stars_lab = label(stars, "status")
            row1.addWidget(stars_lab)
            log.addLayout(row1)
            log.addWidget(label(fin, "faint"))
            log.addWidget(label(note, "sans", wrap=True))
        log.addStretch()
        log_holder = QWidget()
        log_holder.setLayout(log)
        log_holder.setMinimumWidth(420)
        split.addWidget(log_holder, 1)

        # recommendations
        recs = QVBoxLayout()
        rhead = QHBoxLayout()
        title_lab = label("Suggested next", "h2")
        title_lab.setStyleSheet(f"color: {theme.BOOK};")
        rhead.addWidget(title_lab)
        rhead.addWidget(label("3 · from the LLM", "sub"))
        rhead.addStretch()
        recs.addLayout(rhead)

        box = QFrame()
        box.setStyleSheet(
            f"background: #16141f; border: 1px dashed #3b3155; border-radius: 9px;")
        bv = QVBoxLayout(box)
        bv.setContentsMargins(14, 9, 14, 12)
        eye = label("◆ SUGGESTED — NOT YET READ", "eyebrow")
        eye.setStyleSheet("color: #6b5d8f;")
        bv.addWidget(eye)
        for title, author, why in RECS:
            row1 = QHBoxLayout()
            t_lab = label(title, "secondary")
            t_lab.setStyleSheet("color: #c9b8f0;")
            row1.addWidget(t_lab)
            a_lab = label(author, "faint")
            row1.addWidget(a_lab)
            row1.addStretch()
            bv.addLayout(row1)
            why_lab = label(why, "sans", wrap=True)
            why_lab.setStyleSheet(f"color: {theme.BOOK_DIM}; font-size: 12px;")
            bv.addWidget(why_lab)
            add = button("+ add to log", "ghost")
            bv.addWidget(add)
        bv.addWidget(label("refreshed from your catalog · Le Guin + hard-SF signal", "faint"))
        recs.addWidget(box)
        recs.addStretch()
        recs_holder = QWidget()
        recs_holder.setLayout(recs)
        recs_holder.setFixedWidth(360)
        split.addWidget(recs_holder)
