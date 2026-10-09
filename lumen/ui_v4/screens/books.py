"""Books: the reading log beside Lumen's "what to read next" suggestions.

Same data flow as ui_v3's Books screen: `state.books` / `state.recs`,
refreshed on every visit (`refresh_books`), `add_book` from the form,
`recommend_books` for fresh suggestions, and "Add to log" on a suggestion
prefills the form. New here: a search box that filters the log locally.
"""
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QBoxLayout, QWidget

from .. import theme as T
from ..components import (
    Badge, Button, Card, Divider, EmptyState, Eyebrow, Heading, IconLabel,
    Label, ModelOffNotice, ScreenHeader, ScrollArea, SearchField,
    SegmentedControl, SkeletonRow, TextField, TypingDots, clear_layout, hbox,
    vbox,
)

NARROW = 820          # content width below which the two columns stack
REC_W = 340
RECS_TIMEOUT_MS = 120_000


def _strong(lbl: Label) -> Label:
    f = lbl.font()
    f.setWeight(QFont.Weight.DemiBold)
    lbl.setFont(f)
    return lbl


class _Stars(QWidget):
    """Read-only rating: five outlined/filled stars with a spoken label."""

    def __init__(self, rating: int, size: int = T.ICON_SM):
        super().__init__()
        rating = max(0, min(5, int(rating or 0)))
        h = hbox(self, (0, 0, 0, 0), 2)
        for i in range(5):
            on = i < rating
            h.addWidget(IconLabel("star-fill" if on else "star",
                                  "accent" if on else "border_strong", size))
        text = f"Rated {rating} out of 5" if rating else "Not rated"
        self.setToolTip(text)
        self.setAccessibleName(text)


class BooksScreen(QWidget):
    def __init__(self, window):
        super().__init__()
        self.win = window
        self.state = window.state
        self._busy = False
        self._query = ""
        self._narrow = False
        self._loaded = (not self.state.live) or bool(self.state.books)
        try:
            from .settings import install_text_size
            install_text_size()
        except Exception:
            pass

        outer = vbox(self, (0, 0, 0, 0), 0)
        self.scroll = ScrollArea(m=(T.S8, T.S6, T.S8, T.S8), s=T.S6)
        outer.addWidget(self.scroll, 1)
        page = self.scroll.lay

        self.header = ScreenHeader(
            "Books", "Your reading log, and what Lumen thinks you'd enjoy next.")
        page.addWidget(self.header)

        host = QWidget()
        self.row = QBoxLayout(QBoxLayout.Direction.LeftToRight, host)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(T.S8)
        page.addWidget(host)
        page.addStretch(1)

        # ---- left: add form + log ----------------------------------------
        left = QWidget()
        lv = vbox(left, (0, 0, 0, 0), T.S5)
        lv.addWidget(self._add_form())

        bar = hbox(s=T.S3)
        log_title = Heading("Reading log", 2)
        bar.addWidget(log_title)
        bar.addStretch(1)
        self.count = Label("", "mono")
        bar.addWidget(self.count, 0, Qt.AlignmentFlag.AlignBottom)
        lv.addLayout(bar)
        self.search = SearchField("Search by title, author or note…")
        self.search.search.connect(self._on_search)
        lv.addWidget(self.search)
        self.log_host = QWidget()
        self.log_lay = vbox(self.log_host, (0, 0, 0, 0), 0)
        lv.addWidget(self.log_host)
        lv.addStretch(1)
        self.row.addWidget(left, 1)

        # ---- right: suggestions ------------------------------------------
        self.rec_card = Card(padding=T.S5, spacing=T.S3)
        self.rec_card.setFixedWidth(REC_W)
        self.row.addWidget(self.rec_card, 0, Qt.AlignmentFlag.AlignTop)

        self._recs_timer = QTimer(self)
        self._recs_timer.setSingleShot(True)
        self._recs_timer.setInterval(RECS_TIMEOUT_MS)
        self._recs_timer.timeout.connect(self._recs_timed_out)

        self.state.books_changed.connect(self._on_books)
        self.state.recs_changed.connect(self._build_recs)
        self.state.model_state_changed.connect(self._build_recs)
        self._build_log()
        self._build_recs()

    # ---- hooks --------------------------------------------------------------
    def on_shown(self):
        self.state.refresh_books()

    def refresh(self):
        self.state.refresh_books()

    def ask_context(self):
        return {"screen": "books",
                "recent": [b.get("title", "") for b in self.state.books[:10]]}

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        narrow = self.width() < NARROW
        if narrow == self._narrow:
            return
        self._narrow = narrow
        if narrow:
            self.row.setDirection(QBoxLayout.Direction.TopToBottom)
            self.rec_card.setMinimumWidth(0)
            self.rec_card.setMaximumWidth(16777215)
        else:
            self.row.setDirection(QBoxLayout.Direction.LeftToRight)
            self.rec_card.setFixedWidth(REC_W)
        # Title + author side by side only while there's room for both.
        self._form_row.setDirection(QBoxLayout.Direction.TopToBottom if narrow
                                    else QBoxLayout.Direction.LeftToRight)

    # ---- add form -----------------------------------------------------------
    def _add_form(self) -> QWidget:
        card = Card(padding=T.S5, spacing=T.S4)
        card.lay.addWidget(Heading("Log a book you finished", 3))

        r1 = QWidget()
        self._form_row = QBoxLayout(QBoxLayout.Direction.LeftToRight, r1)
        self._form_row.setContentsMargins(0, 0, 0, 0)
        self._form_row.setSpacing(T.S3)
        self.f_title = TextField("Title", placeholder="e.g. Piranesi")
        self.f_author = TextField("Author", placeholder="e.g. Susanna Clarke")
        self._form_row.addWidget(self.f_title, 3)
        self._form_row.addWidget(self.f_author, 2)
        card.lay.addWidget(r1)

        rate = QWidget()
        rv = vbox(rate, (0, 0, 0, 0), 6)
        rl = Label("Your rating (out of 5)", "field-label")
        rv.addWidget(rl)
        self.f_rating = SegmentedControl(
            [(str(n), str(n)) for n in (1, 2, 3, 4, 5)], "5",
            accessible_name="Your rating, out of 5")
        rv.addWidget(self.f_rating, 0, Qt.AlignmentFlag.AlignLeft)
        card.lay.addWidget(rate)

        self.f_notes = TextField(
            "Notes", helper="Optional. What stuck with you? Lumen uses this "
                            "to pick better suggestions.",
            placeholder="A line or two")
        self.f_notes.input.returnPressed.connect(self._add)
        self.f_title.input.returnPressed.connect(self._add)
        card.lay.addWidget(self.f_notes)

        foot = hbox(s=T.S3)
        foot.addStretch(1)
        self.add_btn = Button("Add to log", "primary", icon="plus",
                              on_click=self._add)
        foot.addWidget(self.add_btn)
        card.lay.addLayout(foot)
        return card

    def _add(self):
        title = self.f_title.text().strip()
        if not title:
            self.f_title.set_error("Add a title so Lumen knows which book.")
            self.f_title.setFocus()
            return
        self.state.add_book(title, self.f_author.text(),
                            int(self.f_rating.value() or 5), self.f_notes.text())
        for f in (self.f_title, self.f_author, self.f_notes):
            f.set_text("")
        self.f_rating.set_value("5")
        self.win.show_toast(f"Added “{title}” to your reading log")

    def _prefill(self, r: dict):
        self.f_title.set_text(r.get("title", ""))
        self.f_author.set_text(r.get("author", ""))
        self.f_title.clear_error()
        self.scroll.verticalScrollBar().setValue(0)
        self.f_notes.setFocus()

    # ---- reading log --------------------------------------------------------
    def _on_books(self):
        self._loaded = True
        self._build_log()

    def _on_search(self, text: str):
        self._query = (text or "").strip().casefold()
        self._build_log()

    def _matches(self, b: dict) -> bool:
        if not self._query:
            return True
        hay = " ".join(str(b.get(k, "")) for k in ("title", "author", "notes"))
        return self._query in hay.casefold()

    def _build_log(self):
        clear_layout(self.log_lay)
        books = self.state.books
        n = len(books)
        self.count.setText(f"{n} book{'' if n == 1 else 's'}" if self._loaded else "")

        if not self._loaded:
            for _ in range(4):
                self.log_lay.addWidget(SkeletonRow(2, height=64))
            return
        if not books:
            self.log_lay.addWidget(EmptyState(
                "books", "Your reading log is empty",
                "Nothing has been logged yet. Add the last book you finished "
                "above and Lumen can start suggesting what to read next.",
                "Log your first book", self.f_title.setFocus))
            return
        shown = [b for b in books if self._matches(b)]
        if not shown:
            self.log_lay.addWidget(EmptyState(
                "search", "No books match that search",
                f"Nothing in your log mentions “{self.search.text().strip()}”.",
                "Clear search", lambda: self.search.setText("")))
            return
        for i, b in enumerate(shown):
            if i:
                self.log_lay.addWidget(Divider())
            self.log_lay.addWidget(self._book_row(b))

    def _book_row(self, b: dict) -> QWidget:
        w = QWidget()
        v = vbox(w, (0, T.S4, 0, T.S4), T.S1)
        top = hbox(s=T.S3)
        title = _strong(Label(b.get("title", ""), "body", wrap=True))
        top.addWidget(title, 1)
        top.addWidget(_Stars(b.get("rating") or 0), 0, Qt.AlignmentFlag.AlignTop)
        v.addLayout(top)
        meta = hbox(s=T.S2)
        if b.get("author"):
            meta.addWidget(Label(b["author"], "small"))
        if b.get("done"):
            if b.get("author"):
                meta.addWidget(Label("·", "muted"))
            meta.addWidget(Label("Finished", "muted"))
            meta.addWidget(Label(str(b["done"]), "meta"))
        meta.addStretch(1)
        v.addLayout(meta)
        if b.get("notes"):
            v.addSpacing(T.S1)
            v.addWidget(Label(b["notes"], "small", wrap=True, selectable=True))
        return w

    # ---- suggestions --------------------------------------------------------
    def _build_recs(self):
        lay = self.rec_card.lay
        clear_layout(lay)
        recs = self.state.recs
        self._busy = False
        self._recs_timer.stop()

        top = hbox(s=T.S2)
        top.addWidget(Eyebrow("Suggested next"), 1)
        if recs:
            top.addWidget(Label(str(len(recs)), "mono"))
        lay.addLayout(top)
        head = hbox(s=T.S2)
        head.addWidget(Heading("Books you haven't read", 3), 1)
        lay.addLayout(head)
        lay.addWidget(self._source_badge(), 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(Label("Picked from your reading log.", "muted", wrap=True))

        if not recs and not self.state.model_enabled:
            lay.addWidget(ModelOffNotice(
                self.state, "Your reading log still works. Suggestions need "
                            "the model."))
        elif not recs:
            lay.addWidget(EmptyState(
                "book", "No suggestions yet",
                "Lumen hasn't suggested anything yet. Ask it to read your log "
                "and pick a few.", "Suggest books", self._refresh))
        for i, r in enumerate(recs):
            lay.addWidget(Divider())
            lay.addWidget(self._rec_row(r))

        lay.addSpacing(T.S1)
        foot = hbox(s=T.S2)
        self.rec_status = TypingDots("Finding books", "muted")
        self.rec_status.setText("")
        foot.addWidget(self.rec_status, 1)
        if recs:
            self.rec_btn = Button("Refresh", "secondary", icon="refresh",
                                  size="sm", on_click=self._refresh)
            self.rec_btn.setToolTip("Ask Lumen for a fresh set of suggestions")
            foot.addWidget(self.rec_btn)
        else:
            self.rec_btn = None
        lay.addLayout(foot)

    def _source_badge(self) -> Badge:
        """Where suggestions come from: a dot and a word, like the sidebar."""
        s = self.state
        mode = getattr(s, "model_mode", "local")
        if not s.model_enabled or mode == "off":
            return Badge("neutral", "Model off")
        if mode == "claude":
            return Badge("info", "Picked by Claude")
        return Badge("success", "Picked on this computer")

    def _rec_row(self, r: dict) -> QWidget:
        w = QWidget()
        v = vbox(w, (0, T.S2, 0, T.S2), T.S1)
        v.addWidget(_strong(Label(r.get("title", ""), "body", wrap=True)))
        if r.get("author"):
            v.addWidget(Label(r["author"], "small"))
        if r.get("why"):
            v.addSpacing(2)
            v.addWidget(Label(r["why"], "muted", wrap=True))
        row = hbox(s=0)
        row.addWidget(Button("Add to log", "ghost", icon="plus", size="sm",
                             on_click=lambda r=r: self._prefill(r)))
        row.addStretch(1)
        v.addLayout(row)
        return w

    def _refresh(self):
        if self._busy:
            return
        if not self.state.model_enabled:
            self._build_recs()          # repaints with the notice in place
            return
        self._busy = True
        self.rec_status.start("Finding books")
        if self.rec_btn is not None:
            self.rec_btn.set_busy(True, "Working…")
        self._recs_timer.start()
        self.state.recommend_books()
        if not self.state.live:
            # Sample mode has no model: settle instead of spinning forever.
            QTimer.singleShot(0, self._build_recs)

    def _recs_timed_out(self):
        if self._busy:
            self._build_recs()
            self.win.show_toast("Suggestions took too long. Try again in a moment.")
