"""Books screen: reading log (grows) | 360px LLM recommendations panel.

Log + recommendations are live from the daemon; recommendations only regenerate
on demand (the Suggest next button)."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QFrame, QLineEdit, QSizePolicy, QWidget

from .. import theme as T
from ..state import AppState
from ..widgets import (
    ElideLabel, button, clear_layout, hbox, hline, label, scroll, vbox,
)


class BooksScreen(QWidget):
    def __init__(self, state: AppState):
        super().__init__()
        self.state = state

        inner = QWidget()
        row = hbox(inner, (26, 22, 26, 40), 24)

        # ---- reading log ----
        log = QWidget()
        log.setMinimumWidth(420)
        lv = vbox(log, (0, 0, 0, 0), 0)
        head = hbox(s=10)
        head.addWidget(label("Reading log", 16, T.TEXT_PRIMARY, 600))
        self.count_lab = label("", 11, T.TEXT_DIM)
        head.addWidget(self.count_lab)
        head.addStretch(1)
        lv.addLayout(head)
        lv.addSpacing(14)

        form = QFrame()
        form.setProperty("cls", "panel")
        fv = vbox(form, (12, 12, 12, 12), 8)
        fv.addWidget(label("+ ADD ENTRY", 10, T.TEXT_FAINT, ls=1))
        fv.addSpacing(1)
        r1 = hbox(s=8)
        self.nb_title = QLineEdit()
        self.nb_title.setPlaceholderText("Title")
        r1.addWidget(self.nb_title, 2)
        self.nb_author = QLineEdit()
        self.nb_author.setPlaceholderText("Author")
        r1.addWidget(self.nb_author, 1)
        self.nb_rating = QComboBox()
        self.nb_rating.addItems(["★5", "★4", "★3", "★2", "★1"])
        self.nb_rating.setCursor(Qt.CursorShape.PointingHandCursor)
        r1.addWidget(self.nb_rating)
        fv.addLayout(r1)
        r2 = hbox(s=8)
        self.nb_notes = QLineEdit()
        self.nb_notes.setPlaceholderText("Notes (free text)")
        r2.addWidget(self.nb_notes, 1)
        log_btn = button("Log", "soft", px=11)
        log_btn.clicked.connect(self._add)
        r2.addWidget(log_btn)
        fv.addLayout(r2)
        lv.addWidget(form)
        lv.addSpacing(18)

        self.log_lay = vbox(s=0)
        lv.addLayout(self.log_lay)
        lv.addStretch(1)
        row.addWidget(log, 1)

        # ---- recommendations ----
        recs = QWidget()
        recs.setFixedWidth(360)
        rv = vbox(recs, (0, 0, 0, 0), 0)
        rhead = hbox(s=8)
        rhead.addWidget(label("Suggested next", 16, T.BOOK, 600))
        self.rec_count = label("", 11, T.TEXT_DIM)
        rhead.addWidget(self.rec_count)
        rhead.addStretch(1)
        self.suggest_btn = button("Suggest next", "rec")
        self.suggest_btn.clicked.connect(self._suggest)
        rhead.addWidget(self.suggest_btn)
        rv.addLayout(rhead)
        rv.addSpacing(14)

        panel = QFrame()
        panel.setProperty("cls", "recpanel")
        pv = vbox(panel, (14, 6, 14, 12), 0)
        eyebrow = QWidget()
        el = vbox(eyebrow, (0, 9, 0, 4), 0)
        el.addWidget(label("◆ SUGGESTED — NOT YET READ", 10, T.BOOK_LABEL, ls=1))
        pv.addWidget(eyebrow)
        self.rec_lay = vbox(s=0)
        pv.addLayout(self.rec_lay)
        foot = QWidget()
        fl = vbox(foot, (0, 9, 0, 0), 0)
        fl.addWidget(label("grounded in your reading log", 10, T.BOOK_FOOT))
        pv.addWidget(foot)
        rv.addWidget(panel)
        rv.addStretch(1)
        row.addWidget(recs, 0, Qt.AlignmentFlag.AlignTop)

        root = vbox(self)
        root.addWidget(scroll(inner), 1)

        state.books_changed.connect(self.populate)
        state.recs_changed.connect(self.populate_recs)
        self.populate()
        self.populate_recs()

    def _add(self):
        rating = 5 - self.nb_rating.currentIndex()
        self.state.add_book(self.nb_title.text(), self.nb_author.text(), rating,
                            self.nb_notes.text())
        self.nb_title.clear()
        self.nb_author.clear()
        self.nb_notes.clear()
        self.nb_rating.setCurrentIndex(0)

    def _add_rec(self, rec: dict):
        self.nb_title.setText(rec["title"])
        self.nb_author.setText(rec["author"])
        self.nb_notes.setFocus()

    def _suggest(self):
        if not self.state.live:
            return
        self.suggest_btn.setEnabled(False)
        self.suggest_btn.setText("generating…")
        self.state.recommend_books(on_done=self._reset_suggest)

    def _reset_suggest(self):
        self.suggest_btn.setEnabled(True)
        self.suggest_btn.setText("Suggest next")

    def populate(self):
        self.count_lab.setText(f"{len(self.state.books)} books · your catalog")
        clear_layout(self.log_lay)
        for b in self.state.books:
            item = QWidget()
            iv = vbox(item, (2, 12, 2, 12), 0)
            top = hbox(s=10)
            top.addWidget(label(b["title"], 14, T.TEXT_PRIMARY, 500))
            if b["author"]:
                top.addWidget(label(b["author"], 11, T.TEXT_MUTED))
            top.addStretch(1)
            if b["rating"]:
                stars = "★" * b["rating"] + "☆" * (5 - b["rating"])
                top.addWidget(label(stars, 12, T.WARN, ls=1))
            iv.addLayout(top)
            if b["done"]:
                iv.addSpacing(3)
                iv.addWidget(label(f"finished {b['done']}", 10, T.TEXT_DIM))
            if b["notes"]:
                iv.addSpacing(5)
                iv.addWidget(label(b["notes"], 12, T.TEXT_BODY_MUTED, sans=True, wrap=True))
            self.log_lay.addWidget(item)
            self.log_lay.addWidget(hline(T.BORDER_FAINTEST))

    def populate_recs(self):
        self.rec_count.setText(f"{len(self.state.recs)} · from the LLM")
        clear_layout(self.rec_lay)
        if not self.state.recs:
            self.rec_lay.addWidget(label("no suggestions yet — press Suggest next",
                                         12, T.BOOK_DIM, sans=True))
            return
        for r in self.state.recs:
            item = QWidget()
            iv = vbox(item, (0, 11, 0, 11), 0)
            top = hbox(s=8)
            title = ElideLabel(r["title"], 13, T.BOOK_TITLE, 500)
            title.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
            top.addWidget(title, 1)
            if r["author"]:
                top.addWidget(label(r["author"], 11, T.BOOK_LABEL))
            iv.addLayout(top)
            iv.addSpacing(4)
            iv.addWidget(label("↳ " + r["why"], 12, T.BOOK_DIM, sans=True, wrap=True))
            iv.addSpacing(7)
            brow = hbox(s=0)
            add = button("+ add to log", "rec")
            add.clicked.connect(lambda _, r=r: self._add_rec(r))
            brow.addWidget(add)
            brow.addStretch(1)
            iv.addLayout(brow)
            self.rec_lay.addWidget(item)
            self.rec_lay.addWidget(hline(T.BOOK_ROW_BORDER))
