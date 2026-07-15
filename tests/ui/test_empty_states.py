from PyQt6.QtWidgets import QLabel
from tests.ui.test_ui_v2 import FakeClient
from lumen.ui_v2.state import AppState


def _texts(w):
    return " | ".join(l.text() for l in w.findChildren(QLabel))


def _state():
    # data=FakeClient → live mode; the client never answers, so lists stay empty.
    return AppState(data=FakeClient(), chat=FakeClient(), confirm=FakeClient())


def test_todos_all_clear_when_empty(qtbot):
    from lumen.ui_v2.screens.todos import TodosScreen
    w = TodosScreen(_state())
    qtbot.addWidget(w)
    assert "All clear" in _texts(w)


def test_books_empty_catalog(qtbot):
    from lumen.ui_v2.screens.books import BooksScreen
    w = BooksScreen(_state())
    qtbot.addWidget(w)
    assert "No books logged yet" in _texts(w)


def test_mail_empty_inbox_when_connected(qtbot):
    from lumen.ui_v2.screens.mail import MailScreen
    state = _state()
    state.mail_connected = True
    w = MailScreen(state)
    qtbot.addWidget(w)
    w.populate()
    assert "No messages" in _texts(w)


def test_mail_not_connected_placeholder(qtbot):
    from lumen.ui_v2.screens.mail import MailScreen
    state = _state()
    state.mail_connected = False
    w = MailScreen(state)
    qtbot.addWidget(w)
    w.populate()
    assert "Gmail not connected" in _texts(w)
