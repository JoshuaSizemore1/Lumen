"""#32 — the suggest-labels review popup (two-pane mini inbox).

Nothing is written during classification, so a ✕ (reject) is purely local and a
✓ (accept) files the message under its suggested label. The popup lists every
suggested message and closes once they're all handled.
"""
from PyQt6.QtWidgets import QWidget

from lumen.ui_v3.state import AppState
from lumen.ui_v3.suggest_review import SuggestReviewOverlay


def _overlay(qtbot, suggestions):
    state = AppState()                              # sample mode, no daemon
    state.mail_suggestions = dict(suggestions)
    state.mail_suggestion_meta = {
        mid: {"id": mid, "from": f"Sender {mid}", "subj": f"Subject {mid}",
              "date": "Jul 21"} for mid in suggestions}
    parent = QWidget()
    parent.resize(1000, 700)
    qtbot.addWidget(parent)
    ov = SuggestReviewOverlay(parent, state)
    ov._keep_parent = parent   # top-level parent would otherwise be GC'd, taking
    ov.open()                  # its C++ children (the overlay's widgets) with it
    return ov, state


def test_open_lists_every_suggestion(qtbot):
    ov, _ = _overlay(qtbot, {"m1": "Bills", "m2": "Work", "m3": "Bills"})
    assert set(ov._shown) == {"m1", "m2", "m3"}
    assert ov.count.text() == "3 to review"


def test_rows_clustered_by_label(qtbot):
    ov, _ = _overlay(qtbot, {"m1": "Work", "m2": "Bills", "m3": "Work"})
    # sorted by (label, subject): both Bills/Work clusters together.
    assert ov._shown == ["m2", "m1", "m3"]


def test_accept_files_and_removes_row(qtbot):
    ov, state = _overlay(qtbot, {"m1": "Bills", "m2": "Work"})
    ov._accept("m1")
    assert "m1" not in state.mail_suggestions          # filed → gone
    assert "m1" not in state.mail_suggestion_meta
    assert ov._shown == ["m2"]


def test_reject_drops_row_without_writing(qtbot):
    ov, state = _overlay(qtbot, {"m1": "Bills", "m2": "Work"})
    ov._reject("m2")
    assert "m2" not in state.mail_suggestions
    assert ov._shown == ["m1"]


def test_handling_last_suggestion_closes_popup(qtbot):
    ov, _ = _overlay(qtbot, {"m1": "Bills"})
    assert ov._open
    ov._reject("m1")
    assert ov._open is False


def test_dismiss_clears_all_without_writing(qtbot):
    ov, state = _overlay(qtbot, {"m1": "Bills", "m2": "Work"})
    ov._dismiss()
    assert state.mail_suggestions == {}
    assert ov._open is False


def test_accept_all_files_every_suggestion(qtbot):
    ov, state = _overlay(qtbot, {"m1": "Bills", "m2": "Work"})
    ov._accept_all()
    assert state.mail_suggestions == {}
    assert ov._open is False


def test_selecting_a_row_changes_open_message(qtbot):
    ov, _ = _overlay(qtbot, {"m1": "Bills", "m2": "Work"})
    ov._select("m2")
    assert ov._sel == "m2"
