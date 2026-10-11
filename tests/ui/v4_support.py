"""Shared fixtures for the ui_v4 tests. Import the fixtures you use:

    from tests.ui.v4_support import FakeClient, v4_settings, make_window  # noqa: F401

`v4_settings` must be used by every test that builds a v4 window:
`theme.settings()` is QSettings("lumen", "ui_v4"), Josh's real preference
store, and the window writes `last_screen` / `geometry` to it.
"""
import os

os.environ.setdefault("LUMEN_REDUCED_MOTION", "1")

import pytest
from PyQt6.QtCore import QObject, QSettings, pyqtSignal
from PyQt6.QtWidgets import QApplication

import lumen.ui_v4.theme as T


class FakeClient(QObject):
    """Records every request/send; mirrors DaemonClient's signals."""
    chunk = pyqtSignal(str)
    done = pyqtSignal()
    error = pyqtSignal(str)
    tool_used = pyqtSignal(str)
    cold_start = pyqtSignal()
    model_off = pyqtSignal()
    via = pyqtSignal(str)
    claude_unavailable = pyqtSignal(str, str)
    conversation = pyqtSignal(int)
    captured = pyqtSignal(dict)
    confirm_requested = pyqtSignal(dict)
    compose_requested = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.requests: list[tuple] = []
        self.sent: list[tuple] = []
        self.errors: list[tuple] = []     # (type, payload, on_error)

    def request(self, type_, payload, cb, on_error=None):
        self.requests.append((type_, payload, cb))
        if on_error is not None:
            self.errors.append((type_, payload, on_error))

    def send(self, type_, payload):
        self.sent.append((type_, payload))

    def sleep_model(self):
        self.sent.append(("sleep", {}))

    def respond_confirm(self, confirm_id, approved, check=None):
        self.sent.append(("confirm.response", {"confirm_id": confirm_id,
                                               "approved": approved}))

    def request_types(self) -> list[str]:
        return [t for t, _p, _cb in self.requests]

    def last(self, type_):
        """(payload, cb) of the most recent request of this type, or None."""
        for t, p, cb in reversed(self.requests):
            if t == type_:
                return p, cb
        return None

    def fail(self, *types, msg="boom"):
        """Answer the most recent request of any of these types with a daemon
        error. Returns False if there was none."""
        for t, _p, on_error in reversed(self.errors):
            if t in types:
                on_error(msg)
                return True
        return False


@pytest.fixture(autouse=True)
def _stub_canvas_creds(monkeypatch):
    """The Canvas screen must never probe the OS keyring in tests."""
    from lumen.ui_v3 import canvas_creds
    monkeypatch.setattr(canvas_creds, "prime", lambda *a, **kw: None)
    monkeypatch.setattr(canvas_creds, "_has_saved", False, raising=False)
    monkeypatch.setattr(canvas_creds, "_probing", False, raising=False)


@pytest.fixture
def v4_settings(monkeypatch, tmp_path):
    """Point every ui_v4 QSettings at a temp INI file; returns a factory for
    reading it back."""
    ini = str(tmp_path / "ui_v4.ini")

    def make():
        return QSettings(ini, QSettings.Format.IniFormat)
    v3_ini = str(tmp_path / "ui_v3.ini")
    monkeypatch.setattr(T, "settings", make)
    # The first-run text-size migration reads ui_v3's store; keep it off
    # Josh's real one too.
    monkeypatch.setattr(T, "_v3_settings",
                        lambda: QSettings(v3_ini, QSettings.Format.IniFormat))
    monkeypatch.setattr(T, "_manager", None)   # rebuild from patched settings
    return make


def make_window(qtbot, state=None, chat_client=None, size=(1280, 800),
                show=True):
    from lumen.ui_v4.main import LumenWindow
    T.load_fonts()
    T.manager().apply(QApplication.instance())
    w = LumenWindow(state, chat_client)
    qtbot.addWidget(w)
    w.resize(*size)
    if show:
        w.show()
    return w
