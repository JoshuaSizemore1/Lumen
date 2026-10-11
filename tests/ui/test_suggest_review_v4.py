"""v4 suggested-labels review (todo-fixes #107): Esc, an outside click and the
X close it but keep every suggestion waiting behind the Inbox banner — they
can be a 40-message model run. Only "Dismiss all" drops them."""
import pytest
from PyQt6.QtCore import QPoint, Qt

from lumen.ui_v3.state import AppState
from tests.ui.v4_support import (  # noqa: F401
    FakeClient, _stub_canvas_creds, make_window, v4_settings,
)


@pytest.fixture
def review(qtbot, v4_settings):
    win = make_window(qtbot, AppState(data=FakeClient()))
    st = win.state
    st.mail_suggestions = {"m1": "Bills", "m2": "School"}
    st.mail_suggestion_meta = {"m1": {"from": "a@x.com", "subj": "Invoice"},
                               "m2": {"from": "b@y.com", "subj": "Field trip"}}
    win.suggest_review.open()
    assert win.suggest_review.isVisible()
    return win


def test_esc_keeps_the_suggestions(review):
    review.suggest_review.cancel()
    assert not review.suggest_review.isVisible()
    assert review.state.mail_suggestions == {"m1": "Bills", "m2": "School"}


def test_outside_click_keeps_the_suggestions(review, qtbot):
    qtbot.mouseClick(review.suggest_review, Qt.MouseButton.LeftButton,
                     pos=QPoint(2, 2))
    assert not review.suggest_review.isVisible()
    assert len(review.state.mail_suggestions) == 2


def test_reopening_shows_them_again(review):
    review.suggest_review.cancel()
    review.suggest_review.open()
    assert review.suggest_review.isVisible()
    assert review.suggest_review._shown == ["m1", "m2"]


def test_dismiss_all_drops_them(review):
    review.suggest_review._dismiss()
    assert not review.suggest_review.isVisible()
    assert review.state.mail_suggestions == {}
