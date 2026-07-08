from PyQt6.QtCore import QObject, Qt, pyqtSignal

from lumen.ui.launcher import LauncherScreen


class FakeClient(QObject):
    chunk = pyqtSignal(str)
    done = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.sent = []

    def send(self, type_, payload):
        self.sent.append((type_, payload))


def make(qtbot):
    client = FakeClient()
    w = LauncherScreen(client)
    qtbot.addWidget(w)
    w.show()
    return w, client


def test_enter_sends_chat(qtbot):
    w, client = make(qtbot)
    w.input.setText("what is a monad")
    qtbot.keyClick(w.input, Qt.Key.Key_Return)
    assert client.sent == [("chat", {"message": "what is a monad"})]
    assert not w.hints.isVisible()          # hints clear once a query is running


def test_chunks_stream_into_response(qtbot):
    w, client = make(qtbot)
    w.input.setText("hi")
    qtbot.keyClick(w.input, Qt.Key.Key_Return)
    client.chunk.emit("Hel")
    client.chunk.emit("lo.")
    client.done.emit()
    assert w.response.toPlainText() == "Hello."
    assert w.response.isVisible()
    assert not w.status.isVisible()


def test_wake_state_appears_when_slow(qtbot):
    w, client = make(qtbot)
    w.input.setText("hi")
    qtbot.keyClick(w.input, Qt.Key.Key_Return)
    qtbot.wait(1700)                        # exceed the 1500ms cold-start threshold
    assert w.status.isVisible()
    assert "waking model" in w.status.text()
    client.chunk.emit("x")
    assert not w.status.isVisible()


def test_error_state(qtbot):
    w, client = make(qtbot)
    w.input.setText("hi")
    qtbot.keyClick(w.input, Qt.Key.Key_Return)
    client.error.emit("daemon offline — start it")
    assert w.status.isVisible()
    assert "daemon offline" in w.status.text()
