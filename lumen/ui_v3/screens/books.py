"""Books — reading log beside the purple "Suggested next" panel.

The recommendation panel runs its own purple scheme in the mock, independent of
the app accent; that is preserved. The regenerate control is Lumen's (the mock
only ever showed a static "refreshed" caption).
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QLineEdit, QWidget

from .. import theme as T
from ..widgets import (
    ClickLabel, Select, TypingDots, button, clear_layout, eyebrow, font, hbox,
    hline, label, scroll, vbox,
)


class BooksScreen(QWidget):
    def __init__(self, state):
        super().__init__()
        self.setObjectName("screen")
        self.state = state
        self._busy = False

        outer = vbox(self, (0, 0, 0, 0), 0)
        host = QWidget()
        self.row = hbox(host, (34, 26, 34, 44), 30)
        outer.addWidget(scroll(host), 1)

        self.log_host = QWidget()
        self.log_lay = vbox(self.log_host, (0, 0, 0, 0), 0)
        self.log_host.setMinimumWidth(440)
        self.row.addWidget(self.log_host, 1)          # greedy

        self.rec_host = QWidget()
        self.rec_host.setFixedWidth(T.BOOK_REC_W)
        self.rec_lay = vbox(self.rec_host, (0, 0, 0, 0), 0)
        self.row.addWidget(self.rec_host, 0, Qt.AlignmentFlag.AlignTop)

        state.books_changed.connect(self._build_log)
        state.recs_changed.connect(self._build_recs)
        self._build_log()
        self._build_recs()

    def showEvent(self, ev):
        super().showEvent(ev)
        self.state.refresh_books()

    # ---- reading log ------------------------------------------------------
    def _build_log(self):
        clear_layout(self.log_lay)
        v = self.log_lay
        books = self.state.books

        head = hbox(m=(0, 0, 0, 12), s=12)
        head.addWidget(label("Reading log", 25, T.TEXT_PRIMARY, 600, ls=-0.4))
        head.addStretch(1)
        head.addWidget(label(f"{len(books)} books · your catalog", 10,
                             T.TEXT_FAINT, mono=True))
        v.addLayout(head)
        v.addWidget(hline(T.BORDER_STRONG))
        v.addSpacing(18)

        v.addWidget(self._add_form())
        v.addSpacing(22)

        if not books:
            v.addWidget(label("Nothing logged yet.", 13, T.TEXT_FAINT))
        for b in books:
            v.addWidget(self._book_row(b))
        v.addStretch(1)

    def _add_form(self) -> QWidget:
        panel = QFrame()
        panel.setProperty("role", "panel")
        v = vbox(panel, (14, 14, 14, 14), 9)
        v.addWidget(eyebrow("Add entry", px=9.5))

        r1 = hbox(s=9)
        self.f_title = QLineEdit()
        self.f_title.setPlaceholderText("Title")
        self.f_title.setProperty("cls", "onpanel")
        self.f_author = QLineEdit()
        self.f_author.setPlaceholderText("Author")
        self.f_author.setProperty("cls", "onpanel")
        self.f_rating = Select(on_panel=True)
        for n in (5, 4, 3, 2, 1):
            self.f_rating.addItem(f"★{n}", n)
        r1.addWidget(self.f_title, 2)
        r1.addWidget(self.f_author, 1)
        r1.addWidget(self.f_rating)
        v.addLayout(r1)

        r2 = hbox(s=9)
        self.f_notes = QLineEdit()
        self.f_notes.setPlaceholderText("Notes (free text)")
        self.f_notes.setProperty("cls", "onpanel")
        self.f_notes.returnPressed.connect(self._add)
        r2.addWidget(self.f_notes, 1)
        log_b = button("Log", "primary", px=14, height=32)
        log_b.clicked.connect(self._add)
        r2.addWidget(log_b)
        v.addLayout(r2)
        return panel

    def _book_row(self, b: dict) -> QWidget:
        w = QWidget()
        v = vbox(w, (2, 14, 2, 14), 0)
        top = hbox(s=11)
        top.addWidget(label(b.get("title", ""), 18, T.TEXT_PRIMARY, 500))
        top.addWidget(label(b.get("author", ""), 11, T.TEXT_MUTED, mono=True))
        top.addStretch(1)
        rating = int(b.get("rating") or 0)
        stars = label("★" * rating + "☆" * (5 - rating), 12, T.STAR, ls=1)
        top.addWidget(stars)
        v.addLayout(top)
        if b.get("done"):
            v.addSpacing(4)
            v.addWidget(label(f"finished {b['done']}", 10, T.TEXT_FAINT,
                              mono=True))
        if b.get("notes"):
            v.addSpacing(6)
            v.addWidget(label(b["notes"], 13.5, T.TEXT_SECONDARY, wrap=True))
        v.addSpacing(14)
        v.addWidget(hline(T.BORDER_FAINT))
        return w

    def _add(self):
        title = self.f_title.text().strip()
        if not title:
            return
        self.state.add_book(title, self.f_author.text(),
                            self.f_rating.currentData(), self.f_notes.text())
        for f in (self.f_title, self.f_author, self.f_notes):
            f.clear()
        self.f_rating.setCurrentIndex(0)

    # ---- recommendations --------------------------------------------------
    def _build_recs(self):
        clear_layout(self.rec_lay)
        v = self.rec_lay
        recs = self.state.recs
        self._busy = False

        head = hbox(m=(0, 0, 0, 12), s=9)
        head.addWidget(label("Suggested next", 20, T.BOOK_ACCENT, 600, ls=-0.3))
        head.addStretch(1)
        head.addWidget(label(f"{len(recs)} · LLM", 10, T.TEXT_FAINT, mono=True))
        v.addLayout(head)
        v.addWidget(hline(T.BOOK_BORDER))
        v.addSpacing(18)

        panel = QFrame()
        panel.setProperty("role", "books")
        pv = vbox(panel, (16, 8, 16, 14), 0)
        pv.addWidget(label("◆ NOT YET READ", 9, T.BOOK_LABEL, mono=True, ls=1.5))
        pv.addSpacing(4)

        if not recs:
            pv.addWidget(label("No recommendations yet — press refresh.", 12.5,
                               T.BOOK_WHY, wrap=True))
        for i, r in enumerate(recs):
            if i:
                pv.addWidget(hline(T.BOOK_ROW_BORDER))
            pv.addWidget(self._rec_row(r))

        pv.addSpacing(11)
        foot = hbox(s=8)
        self.rec_status = TypingDots("", 9, T.BOOK_FOOT)
        self.rec_status.setText("from your reading log")
        foot.addWidget(self.rec_status)
        foot.addStretch(1)
        refresh = ClickLabel("↻ refresh", 9, T.BOOK_ACCENT, mono=True,
                             on_click=self._refresh)
        foot.addWidget(refresh)
        pv.addLayout(foot)
        v.addWidget(panel)
        v.addStretch(1)

    def _rec_row(self, r: dict) -> QWidget:
        w = QWidget()
        v = vbox(w, (0, 12, 0, 12), 0)
        top = hbox(s=9)
        top.addWidget(label(r.get("title", ""), 16, T.BOOK_TITLE, 500))
        top.addWidget(label(r.get("author", ""), 10.5, T.BOOK_LABEL, mono=True))
        top.addStretch(1)
        v.addLayout(top)
        if r.get("why"):
            v.addSpacing(5)
            v.addWidget(label(f"↳ {r['why']}", 12.5, T.BOOK_WHY, wrap=True))
        v.addSpacing(9)
        add = button("+ add to log", "books")
        add.clicked.connect(lambda: self._prefill(r))
        row = hbox(s=0)
        row.addWidget(add)
        row.addStretch(1)
        v.addLayout(row)
        return w

    def _prefill(self, r: dict):
        self.f_title.setText(r.get("title", ""))
        self.f_author.setText(r.get("author", ""))
        self.f_notes.setFocus()

    def _refresh(self):
        if self._busy:
            return
        self._busy = True
        self.rec_status.start("◇ thinking")
        self.state.recommend_books()
