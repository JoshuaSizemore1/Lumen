"""Books: live reading log + grounded 'suggested next' panel. All data via daemon
one-shots; recommendations only generate on demand (Suggest next button or chat).
Recs panel is deliberately visually distinct (dashed purple) from the log."""

from datetime import date

from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from lumen.ui import theme
from lumen.ui.widgets import ClickableLabel, Panel, button, label


def format_finished(iso: str | None) -> str:
    if not iso:
        return ""
    d = date.fromisoformat(iso)
    return f"finished {d.strftime('%b')} {d.day}"


class StarRating(QWidget):
    """Five clickable stars; value() is 1-5 or None. Clicking the current value clears."""

    def __init__(self):
        super().__init__()
        self._value: int | None = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self._stars: list[ClickableLabel] = []
        for i in range(1, 6):
            s = ClickableLabel("☆", "status")
            s.clicked.connect(lambda v=i: self._clicked(v))
            self._stars.append(s)
            lay.addWidget(s)

    def value(self) -> int | None:
        return self._value

    def set_value(self, v: int | None) -> None:
        self._value = v
        for i, s in enumerate(self._stars, start=1):
            s.setText("★" if v is not None and i <= v else "☆")

    def _clicked(self, v: int) -> None:
        self.set_value(None if v == self._value else v)


class BooksScreen(QWidget):
    def __init__(self, client):
        super().__init__()
        self._client = client
        self._books: list[dict] = []
        self._recs: dict = {"recs": [], "generated_at": None}
        self._loaded = False
        client.error.connect(self._on_error)

        split = QHBoxLayout(self)
        split.setContentsMargins(26, 22, 26, 22)
        split.setSpacing(24)

        # reading log (left)
        log = QVBoxLayout()
        head = QHBoxLayout()
        head.addWidget(label("Reading log", "h2"))
        self.count_label = label("", "sub")
        head.addWidget(self.count_label)
        head.addStretch()
        log.addLayout(head)

        self.status = label("", "status")
        self.status.hide()
        log.addWidget(self.status)

        form = Panel()
        fv = QVBoxLayout(form)
        fv.setContentsMargins(12, 12, 12, 12)
        fv.addWidget(label("+ ADD ENTRY", "eyebrow"))
        line1 = QHBoxLayout()
        self.title_field = QLineEdit()
        self.title_field.setProperty("kind", "field")
        self.title_field.setPlaceholderText("Title")
        line1.addWidget(self.title_field, 2)
        self.author_field = QLineEdit()
        self.author_field.setProperty("kind", "field")
        self.author_field.setPlaceholderText("Author")
        line1.addWidget(self.author_field, 1)
        self.stars = StarRating()
        line1.addWidget(self.stars)
        fv.addLayout(line1)
        line2 = QHBoxLayout()
        self.notes_field = QLineEdit()
        self.notes_field.setProperty("kind", "field")
        self.notes_field.setPlaceholderText("Notes (free text)")
        self.notes_field.returnPressed.connect(self._add)
        line2.addWidget(self.notes_field, 1)
        log_btn = button("Log", "soft")
        log_btn.clicked.connect(self._add)
        line2.addWidget(log_btn)
        fv.addLayout(line2)
        log.addWidget(form)

        self._list_area = QWidget()
        log.addWidget(self._list_area)
        log.addStretch()
        self._log_layout = log
        log_holder = QWidget()
        log_holder.setLayout(log)
        log_holder.setMinimumWidth(420)
        split.addWidget(log_holder, 1)

        # suggested next (right) — content rebuilt in _rebuild_recs
        recs = QVBoxLayout()
        rhead = QHBoxLayout()
        title_lab = label("Suggested next", "h2")
        title_lab.setStyleSheet(f"color: {theme.BOOK};")
        rhead.addWidget(title_lab)
        rhead.addWidget(label("grounded in your log", "sub"))
        rhead.addStretch()
        recs.addLayout(rhead)
        self.suggest_btn = button("Suggest next", "soft")
        self.suggest_btn.clicked.connect(self._suggest)
        recs.addWidget(self.suggest_btn)
        self._recs_area = QWidget()
        recs.addWidget(self._recs_area)
        recs.addStretch()
        self._recs_layout = recs
        recs_holder = QWidget()
        recs_holder.setLayout(recs)
        recs_holder.setFixedWidth(360)
        split.addWidget(recs_holder)

        self._rebuild_log()
        self._rebuild_recs()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._loaded:
            self._client.request("books.list", {}, self._set_books)
            self._client.request("books.recs", {}, self._set_recs)

    # ----- log half -----

    def _add(self) -> None:
        title = self.title_field.text().strip()
        if not title:
            return
        self._client.request("books.add", {
            "title": title,
            "author": self.author_field.text().strip(),
            "rating": self.stars.value(),
            "notes": self.notes_field.text().strip(),
        }, self._on_added)

    def _on_added(self, rows: list[dict]) -> None:
        self.title_field.clear()
        self.author_field.clear()
        self.notes_field.clear()
        self.stars.set_value(None)
        self._set_books(rows)

    def _delete(self, book_id: int) -> None:
        self._client.request("books.delete", {"id": book_id}, self._set_books)

    def _set_books(self, rows: list[dict]) -> None:
        self._loaded = True
        self._books = rows
        self.status.hide()
        self._rebuild_log()

    def _on_error(self, msg: str) -> None:
        self.status.setText(msg)
        self.status.show()
        self._reset_suggest()

    def _rebuild_log(self) -> None:
        n = len(self._books)
        self.count_label.setText(f"{n} book{'s' if n != 1 else ''} · your catalog")
        fresh = QWidget()
        lay = QVBoxLayout(fresh)
        lay.setContentsMargins(0, 0, 0, 0)
        if not self._books:
            lay.addWidget(label("no books logged yet", "dim"))
        for b in self._books:
            row1 = QHBoxLayout()
            row1.addWidget(label(b["title"], "secondary"))
            if b["author"]:
                row1.addWidget(label(b["author"], "muted"))
            row1.addStretch()
            if b["rating"]:
                row1.addWidget(label("★" * b["rating"] + "☆" * (5 - b["rating"]),
                                     "status"))
            x = ClickableLabel("✕", "faint")
            x.clicked.connect(lambda bid=b["id"]: self._delete(bid))
            row1.addWidget(x)
            lay.addLayout(row1)
            if b["date_finished"]:
                lay.addWidget(label(format_finished(b["date_finished"]), "faint"))
            if b["notes"]:
                lay.addWidget(label(b["notes"], "sans", wrap=True))
        self._log_layout.replaceWidget(self._list_area, fresh)
        self._list_area.deleteLater()
        self._list_area = fresh

    # ----- recs half (behavior lands with the recs-panel task) -----

    def _suggest(self) -> None:
        pass

    def _reset_suggest(self) -> None:
        pass

    def _set_recs(self, result: dict) -> None:
        self._recs = result
        self._rebuild_recs()

    def _rebuild_recs(self) -> None:
        pass
