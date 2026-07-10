from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QLabel

from lumen.ui.book_catalog import BooksScreen, StarRating


def book(id=1, title="Piranesi", author="Susanna Clarke", rating=4,
         notes="quiet", date_finished="2026-04-30"):
    return {"id": id, "title": title, "author": author, "rating": rating,
            "notes": notes, "date_finished": date_finished, "tags": [],
            "created_at": "2026-04-30T10:00:00"}


class FakeClient(QObject):
    error = pyqtSignal(str)

    def __init__(self, books=None, recs=None):
        super().__init__()
        self.books = books if books is not None else []
        self.recs = recs if recs is not None else {"recs": [], "generated_at": None}
        self.requests: list[tuple[str, dict]] = []
        self.held: dict[str, object] = {}   # type_ -> on_result, for manual firing

    def request(self, type_, payload, on_result):
        self.requests.append((type_, payload))
        if type_ in ("books.list", "books.add", "books.delete"):
            on_result(self.books)
        elif type_ == "books.recs":
            on_result(self.recs)
        else:                                # books.recommend: fire manually
            self.held[type_] = on_result


def make_screen(qtbot, books=None, recs=None):
    client = FakeClient(books, recs)
    screen = BooksScreen(client)
    qtbot.addWidget(screen)
    screen.show()   # fires showEvent -> books.list + books.recs
    return screen, client


def texts(widget) -> str:
    return " | ".join(lab.text() for lab in widget.findChildren(QLabel))


def test_show_loads_books_and_recs(qtbot):
    screen, client = make_screen(qtbot, books=[book()])
    types = [t for t, _ in client.requests]
    assert "books.list" in types and "books.recs" in types
    t = texts(screen)
    assert "Piranesi" in t and "Susanna Clarke" in t and "quiet" in t
    assert "★★★★☆" in t


def test_empty_log_shows_placeholder(qtbot):
    screen, _ = make_screen(qtbot)
    assert "no books logged yet" in texts(screen)


def test_add_sends_payload_and_clears_form(qtbot):
    screen, client = make_screen(qtbot)
    screen.title_field.setText("Dune")
    screen.author_field.setText("Frank Herbert")
    screen.stars.set_value(5)
    screen.notes_field.setText("spice")
    screen._add()
    assert ("books.add", {"title": "Dune", "author": "Frank Herbert",
                          "rating": 5, "notes": "spice"}) in client.requests
    assert screen.title_field.text() == "" and screen.stars.value() is None


def test_add_without_title_sends_nothing(qtbot):
    screen, client = make_screen(qtbot)
    screen._add()
    assert not any(t == "books.add" for t, _ in client.requests)


def test_delete_sends_id(qtbot):
    screen, client = make_screen(qtbot, books=[book(id=7)])
    screen._delete(7)
    assert ("books.delete", {"id": 7}) in client.requests


def test_error_shows_status_banner(qtbot):
    screen, client = make_screen(qtbot)
    client.error.emit("daemon offline")
    assert screen.status.isVisible() or screen.status.text() == "daemon offline"


def test_star_rating_click_sets_and_toggle_clears(qtbot):
    stars = StarRating()
    qtbot.addWidget(stars)
    stars._clicked(3)
    assert stars.value() == 3
    stars._clicked(3)
    assert stars.value() is None


RECS = {"recs": [{"title": "Solaris", "author": "Stanislaw Lem",
                  "rationale": "uncanny like Piranesi"}],
        "generated_at": "2026-07-09T12:30:00"}


def test_cached_recs_render_with_generated_stamp(qtbot):
    screen, _ = make_screen(qtbot, recs=RECS)
    t = texts(screen)
    assert "Solaris" in t and "uncanny like Piranesi" in t
    assert "SUGGESTED — NOT YET READ" in t
    assert "generated" in t


def test_no_recs_yet_shows_hint(qtbot):
    screen, _ = make_screen(qtbot)
    assert "press Suggest next" in texts(screen)


def test_suggest_disables_button_until_result(qtbot):
    screen, client = make_screen(qtbot)
    screen.suggest_btn.click()
    assert not screen.suggest_btn.isEnabled()
    assert ("books.recommend", {}) in client.requests
    client.held["books.recommend"](RECS)      # daemon answers
    assert screen.suggest_btn.isEnabled()
    assert "Solaris" in texts(screen)


def test_error_during_suggest_reenables_button(qtbot):
    screen, client = make_screen(qtbot)
    screen.suggest_btn.click()
    assert not screen.suggest_btn.isEnabled()
    client.error.emit("couldn't get grounded suggestions right now — try again")
    assert screen.suggest_btn.isEnabled()


def test_add_to_log_prefills_form(qtbot):
    screen, _ = make_screen(qtbot, recs=RECS)
    screen._prefill(RECS["recs"][0])
    assert screen.title_field.text() == "Solaris"
    assert screen.author_field.text() == "Stanislaw Lem"
