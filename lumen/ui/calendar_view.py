"""Calendar: Month view skeleton. Week/Day + real events arrive in Phase 5."""

from PyQt6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from lumen.ui import theme
from lumen.ui.widgets import button, chip, label

CATEGORY = {"work": theme.ACCENT, "personal": theme.BOOK,
            "health": theme.OK, "social": theme.WARN}
# (day-number, out-of-month, is-today, [(title, category), ...])
WEEKS = [
    [(29, True, False, []), (30, True, False, []), (1, False, False, []), (2, False, False, []),
     (3, False, False, []), (4, False, False, []),
     (5, False, False, [("Long run", "health"), ("Sunday roast @ …", "social")])],
    [(6, False, True, [("Standup — Platf…", "work"), ("1:1 with Priya", "work"), ("Lunch", "personal"),
                       ("Design review: Lumen", "work"), ("Gym", "health")]),
     (7, False, False, [("Standup — Platf…", "work"), ("Sprint planning", "work"), ("Book club: The …", "social")]),
     (8, False, False, [("Standup — Platf…", "work"), ("Deep work — syn…", "work"), ("Lunch w/ Sam", "social")]),
     (9, False, False, [("Standup — Platf…", "work"), ("Dental cleaning", "health"), ("Release review", "work")]),
     (10, False, False, [("Standup — Platf…", "work"), ("Sprint retro", "work"), ("Gym", "health")]),
     (11, False, False, [("Hiking — Ridge …", "social")]), (12, False, False, [])],
    [(13, False, False, []), (14, False, False, [("Quarterly plann…", "work")]), (15, False, False, []),
     (16, False, False, []), (17, False, False, []), (18, False, False, []), (19, False, False, [])],
    [(20, False, False, [("PTO", "personal")]), (21, False, False, []), (22, False, False, []),
     (23, False, False, []), (24, False, False, [("Conf talk: loca…", "work")]),
     (25, False, False, []), (26, False, False, [])],
]


class CalendarScreen(QWidget):
    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QHBoxLayout()
        bar.setContentsMargins(22, 14, 22, 14)
        for text in ("‹", "Today", "›"):
            bar.addWidget(button(text, "ghost"))
        bar.addWidget(label("July 2026", "h2"))
        bar.addStretch()
        for cat, color in CATEGORY.items():
            bar.addWidget(chip(cat, color))
        for i, seg in enumerate(("Month", "Week", "Day")):
            b = button(seg, "soft" if i == 0 else "ghost")
            bar.addWidget(b)
        bar.addWidget(button("+ Event", "primary"))
        root.addLayout(bar)

        wd = QHBoxLayout()
        wd.setContentsMargins(0, 0, 0, 0)
        for d in ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"):
            wd.addWidget(label(d, "eyebrow"), 1)
        root.addLayout(wd)

        grid = QGridLayout()
        grid.setSpacing(0)
        for r, week in enumerate(WEEKS):
            for c, (num, out, today, events) in enumerate(week):
                cell = QFrame()
                cell.setStyleSheet(
                    f"border: 1px solid {theme.BORDER_FAINT};"
                    + (f"background: {theme.ACCENT_SOFT};" if today
                       else f"background: {theme.BG_PANEL_ALT};" if out else ""))
                v = QVBoxLayout(cell)
                v.setContentsMargins(6, 5, 6, 4)
                v.setSpacing(2)
                v.addWidget(label(str(num), "faint" if out else "secondary"))
                for title, cat in events[:3]:
                    v.addWidget(chip(title, CATEGORY[cat]))
                if len(events) > 3:
                    v.addWidget(label(f"+{len(events) - 3} more", "faint"))
                v.addStretch()
                cell.setMinimumHeight(104)
                grid.addWidget(cell, r, c)
        root.addLayout(grid, 1)
