"""#56 — "after the user accepts the suggested labels it should not use the
local Lumen AI to apply them using tooling."

It already doesn't. Every accept path goes straight to `emails.apply_label`,
which is a plain Gmail write through GmailSync; the model is only ever in the
*decision*, never in the *apply*. That was true before this round and is easy
to break by accident later — so it is pinned here rather than rebuilt.

The routes that would mean the model was involved are `chat` (the tool loop)
and `mail.suggest_labels` (the classifier). Neither may appear on an accept.
"""
import pytest
from PyQt6.QtCore import QObject, pyqtSignal

from lumen.ui_v3.state import AppState

MODEL_ROUTES = {"chat", "mail.suggest_labels", "warm"}


class RecordingClient(QObject):
    chunk = pyqtSignal(str)
    done = pyqtSignal()
    error = pyqtSignal(str)
    tool_used = pyqtSignal(str)
    cold_start = pyqtSignal()
    model_off = pyqtSignal()
    conversation = pyqtSignal(int)
    captured = pyqtSignal(dict)
    confirm_requested = pyqtSignal(dict)
    compose_requested = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.requests: list[tuple] = []

    def request(self, type_, payload, cb):
        self.requests.append((type_, payload))
        # Only the label writes get an answer; anything else the shell asks for
        # on construction is left unanswered rather than fed a wrong shape.
        if type_ in ("emails.apply_label", "emails.remove_label"):
            cb({"ok": True, "message": "Labeled"})

    def send(self, type_, payload):
        self.requests.append((type_, payload))

    def routes(self, only=None):
        return [t for t, _p in self.requests if only is None or t == only]


@pytest.fixture
def state():
    st = AppState(data=RecordingClient())
    st.mail_suggestions = {"m1": "Work", "m2": "Bills", "m3": "Work"}
    st.mail_suggestion_meta = {k: {"id": k} for k in st.mail_suggestions}
    return st


def _applies(st):
    return [p for t, p in st._data.requests if t == "emails.apply_label"]


def test_accepting_one_suggestion_writes_the_label_directly(state):
    state.apply_suggestion("m1")
    assert _applies(state) == [{"id": "m1", "label": "Work"}]
    assert not MODEL_ROUTES & set(state._data.routes())


def test_accept_all_writes_one_label_per_message_and_no_model_call(state):
    state.accept_all_suggestions()
    assert _applies(state) == [{"id": "m1", "label": "Work"},
                               {"id": "m2", "label": "Bills"},
                               {"id": "m3", "label": "Work"}]
    assert not MODEL_ROUTES & set(state._data.routes())


def test_accept_all_for_one_label_only_touches_that_label(state):
    state.accept_all_for_label("Work")
    assert _applies(state) == [{"id": "m1", "label": "Work"},
                               {"id": "m3", "label": "Work"}]
    assert state.mail_suggestions == {"m2": "Bills"}   # the rest still pending
    assert not MODEL_ROUTES & set(state._data.routes())


def test_rejecting_writes_nothing_at_all(state):
    before = len(state._data.requests)     # the shell's own startup reads
    state.reject_suggestion("m1")
    state.dismiss_suggestions()
    assert state._data.requests[before:] == []
    assert state.mail_suggestions == {}
