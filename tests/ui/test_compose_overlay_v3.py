"""ui_v3 ComposeOverlay data-shape safety (#26).

The daemon's find-then-send path yields a compose_request whose `to` is a LIST
of resolved addresses. The overlay used to hand that list straight to
QLineEdit.setText (str-only) → TypeError → the app crashed the moment Lumen
found an address and opened the draft. And its fields() returned `to` as a
bare string, which the daemon's _send_email walks character by character.
"""
from PyQt6.QtWidgets import QWidget

from lumen.ui_v3.overlays import ComposeOverlay
from lumen.ui_v3.state import AppState


def _overlay(qtbot):
    parent = QWidget()
    qtbot.addWidget(parent)
    ov = ComposeOverlay(parent, AppState())
    ov._test_parent = parent   # keep the parent alive for the test's lifetime
    qtbot.addWidget(ov)
    return ov


def test_open_accepts_list_recipient_without_crashing(qtbot):
    ov = _overlay(qtbot)
    # The exact shape the daemon compose_request sends for find-then-send.
    ov.open({"to": ["chris@abellalarms.net"], "cc": [], "bcc": [],
             "subject": "Hi", "body": "hello", "compose_id": 5, "reply_to": None})
    assert ov.to.text() == "chris@abellalarms.net"
    assert ov._compose_id == 5


def test_open_accepts_string_recipient(qtbot):
    # The mail screen's Reply passes `to` as a plain string.
    ov = _overlay(qtbot)
    ov.open({"to": "me@example.com", "subject": "Re: x", "body": ""})
    assert ov.to.text() == "me@example.com"


def test_fields_returns_address_list(qtbot):
    ov = _overlay(qtbot)
    ov.open({"to": ["a@x.com", "b@y.com"], "subject": "s", "body": "b",
             "reply_to": "orig123"})
    f = ov.fields()
    assert f["to"] == ["a@x.com", "b@y.com"]
    assert f["reply_to"] == "orig123"
    assert f["cc"] == [] and f["bcc"] == []
