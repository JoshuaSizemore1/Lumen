"""A daemon error or a dropped connection must never leave a v4 screen stuck:
the suggest-labels scrim used to swallow every click until restart (plan H2),
and loading skeletons waited forever for a reply that never came (M2)."""
import pytest

from lumen.ui_v3.state import AppState
from lumen.ui_v4.components import EmptyState, SkeletonRow
from tests.ui.v4_support import (  # noqa: F401
    FakeClient, _stub_canvas_creds, make_window, v4_settings,
)


@pytest.fixture
def live(qtbot, v4_settings):
    data = FakeClient()
    win = make_window(qtbot, AppState(data=data))
    return win, data


def _titles(screen) -> list[str]:
    return [e.title.text() for e in screen.findChildren(EmptyState)
            if e.isVisibleTo(screen)]


def test_suggest_labels_error_releases_the_screen(live, qtbot):
    win, data = live
    win.switch_to("mail")
    mail = win.screen("mail")
    mail._suggest()
    assert mail.busy.isVisible()
    assert data.fail("mail.suggest_labels", msg="classifier crashed")
    assert not mail.busy.isVisible()
    assert mail.suggest_btn.isEnabled()
    assert mail.suggest_btn.text() == "Suggest labels"


def test_mail_list_error_replaces_the_skeleton(live, qtbot):
    win, data = live
    win.switch_to("mail")
    mail = win.screen("mail")
    assert mail.findChildren(SkeletonRow)
    assert data.fail("emails.list", "mail.refresh", msg="daemon offline")
    qtbot.wait(10)
    assert "Couldn't load your mail" in _titles(mail)
    assert not [s for s in mail.list_scroll.findChildren(SkeletonRow)
                if s.isVisibleTo(mail)]


def test_mail_error_clears_on_the_next_good_reply(live, qtbot):
    win, data = live
    win.switch_to("mail")
    assert data.fail("emails.list", "mail.refresh", msg="daemon offline")
    win.state.refresh_mails()
    _payload, cb = data.last("emails.list")
    cb({"emails": [], "connected": True})
    qtbot.wait(10)
    assert win.state.mail_error is None
    assert "Your inbox is empty" in _titles(win.screen("mail"))


def test_stale_list_error_is_ignored(live, qtbot):
    """Only the newest list request's failure counts (#49's rule)."""
    win, data = live
    win.switch_to("mail")
    win.state.refresh_mails()                       # newer than mail.refresh
    assert data.fail("mail.refresh", msg="late failure")
    assert win.state.mail_error is None


def test_calendar_error_shows_an_error_state(live, qtbot):
    win, data = live
    win.switch_to("calendar")
    cal = win.screen("calendar")
    assert data.fail("calendar.list", "calendar.refresh", msg="daemon offline")
    qtbot.wait(10)
    assert "Couldn't load your calendar" in _titles(cal)


def test_todos_error_shows_an_error_state(live, qtbot):
    win, data = live
    win.switch_to("todos")
    todos = win.screen("todos")
    assert data.fail("todos.list", msg="daemon offline")
    qtbot.wait(10)
    assert "Couldn't load your todos" in _titles(todos)


def test_today_cards_settle_on_errors(live, qtbot):
    win, data = live
    win.switch_to("today")
    today = win.screen("today")
    assert today.findChildren(SkeletonRow)
    assert data.fail("todos.list")
    assert data.fail("emails.list", "mail.refresh")
    assert data.fail("calendar.list", "calendar.refresh")
    qtbot.wait(20)
    assert not [s for s in today.findChildren(SkeletonRow)
                if s.isVisibleTo(today)]


def test_briefing_error_settles_the_button(live, qtbot):
    win, data = live
    win.switch_to("today")
    today = win.screen("today")
    today._briefing()
    assert not today.brief_btn.isEnabled()
    assert data.fail("briefing.today", msg="model crashed")
    assert today.brief_btn.isEnabled()
    assert today.brief_text.text() == "model crashed"
