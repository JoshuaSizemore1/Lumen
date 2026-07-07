"""Dashboard: today at a glance. Static skeleton — real data lands in Phases 2/5/6."""

from PyQt6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from lumen.ui import theme
from lumen.ui.widgets import Panel, button, chip, label

TODOS = [("Call the dentist", "personal", False),
         ("Reply to Priya re: sync defaults", "work", False),
         ("Water the plants", "home", True)]
EVENTS = [("09:30", "Standup — Platform", 75, 22, False),
          ("11:00", "1:1 with Priya", 150, 22, False),
          ("13:00", "Lunch", 250, 47, False),
          ("15:30", "Design review: Lumen", 375, 34, True),
          ("18:00", "Gym", 500, 47, False)]
MAIL = [("GitHub", "08:12", "PR #142: swap to local model runtime"),
        ("Priya Nair", "07:40", "Re: sync interval defaults"),
        ("Dr. Okafor's office", "Tue", "Appointment reminder — Jul 9"),
        ("Sarah Chen", "Mon", "Book club: next pick?")]


def _col_head(eye: str, n: str) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 9)
    h.addWidget(label(eye, "accent-eyebrow"))
    h.addStretch()
    h.addWidget(label(n, "dim"))
    return w


class DashboardScreen(QWidget):
    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 24)

        head = QHBoxLayout()
        head.addWidget(label("Mon · Jul 6", "h2"))
        head.addWidget(label("today at a glance", "sub"))
        head.addStretch()
        head.addWidget(label("● synced 2m ago", "faint"))
        root.addLayout(head)

        grid = QGridLayout()
        grid.setSpacing(22)
        grid.setColumnMinimumWidth(0, 290)
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(2, 320)

        # 1 · todos
        todos = QVBoxLayout()
        todos.addWidget(_col_head("TODAY · TODOS", "5 open"))
        for text, tag, done in TODOS:
            row = QHBoxLayout()
            box = QLabel("✓" if done else "")
            box.setFixedSize(15, 15)
            box.setStyleSheet(
                f"background: {theme.ACCENT if done else 'transparent'};"
                f"border: 1px solid {theme.ACCENT if done else theme.TEXT_FAINT}; border-radius: 3px;"
                f"color: {theme.BG_WINDOW}; font-size: 10px;")
            item = label(text, "secondary" if not done else "dim")
            row.addWidget(box)
            row.addWidget(item, 1)
            row.addWidget(chip(tag, theme.TAG_COLORS[tag]))
            todos.addLayout(row)
        todos.addWidget(button("Manage todos →", "link"))
        todos.addStretch()

        # 2 · day calendar: fixed-height panel, absolutely positioned blocks
        cal = QVBoxLayout()
        cal.addWidget(_col_head("TODAY · CALENDAR", "5 events"))
        canvas = Panel("panel-alt")
        canvas.setFixedHeight(620)
        for hour in range(8, 21):                       # 08:00–20:00, 50px/hr
            y = (hour - 8) * 50 + 6
            lab = label(f"{hour:02d}:00", "faint")
            lab.setParent(canvas)
            lab.move(10, y)
            line = QFrame(canvas)
            line.setStyleSheet(f"background: {theme.BORDER_FAINT};")
            line.setGeometry(52, y + 7, 10_000, 1)
        for time, title, top, height, is_next in EVENTS:
            block = QFrame(canvas)
            block.setStyleSheet(
                f"background: {theme.ACCENT_MID if is_next else theme.ACCENT_SOFT};"
                f"border-left: 2px solid {theme.ACCENT}; border-radius: 5px;")
            block.setGeometry(58, top + 6, 480, height)
            v = QVBoxLayout(block)
            v.setContentsMargins(9, 2, 9, 2)
            v.setSpacing(0)
            v.addWidget(label(title, "secondary"))
            v.addWidget(label(f"{time}", "faint"))
        now = QFrame(canvas)
        now.setStyleSheet(f"background: {theme.NOW};")
        now.setGeometry(52, 106, 10_000, 1)
        cal.addWidget(canvas)

        # 3 · unread mail
        mail = QVBoxLayout()
        mail.addWidget(_col_head("UNREAD · MAIL", "4 unread"))
        for sender, when, subj in MAIL:
            top_row = QHBoxLayout()
            top_row.addWidget(label(f"● {sender}", "secondary"))
            top_row.addStretch()
            top_row.addWidget(label(when, "faint"))
            mail.addLayout(top_row)
            mail.addWidget(label(subj, "muted"))
        mail.addWidget(button("Open mail →", "link"))
        mail.addStretch()

        for col, layout in enumerate((todos, cal, mail)):
            holder = QWidget()
            holder.setLayout(layout)
            grid.addWidget(holder, 0, col)
        root.addLayout(grid, 1)
