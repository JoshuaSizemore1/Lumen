"""Performance guards for the Field Notes v4 shell.

A screen that is hidden must NOT rebuild its rows when a background poll fires.
It must rebuild the moment it becomes visible again. This covers the same
regression class that drove ui_v3's perf work: every background mail/todo poll
triggers a full DOM teardown-and-rebuild of potentially hundreds of rows.

Both Mail and Todos implement the same deferral contract:
  * `_on_<data>()` fires on signal; if hidden → set `_dirty = True` and return
  * `on_shown()` checks `_dirty` and calls `rebuild()` if True

If either screen ever rebuilds while hidden, `_dirty` should still be False at
the end (it was cleared by the rebuild that happened). We assert on both the
flag and whether `rebuild` was called, to catch both the "flag works but
rebuild still ran" case and the "flag is wrong" case.
"""
import pytest

from tests.ui.v4_support import _stub_canvas_creds, make_window, v4_settings  # noqa: F401


def _mails(n: int) -> list[dict]:
    return [
        {"id": f"m{i}", "from": f"sender{i}@example.com",
         "subj": f"Subject {i}", "preview": "preview " * 5,
         "time": "9:00", "unread": (i % 3 == 0),
         "body_html": None, "label_names": []}
        for i in range(n)
    ]


def _todos(n: int) -> list[dict]:
    return [
        {"id": f"t{i}", "text": f"Task {i}", "done": False,
         "due_date": None, "tags": [], "group": "none",
         "tag": "", "due": None}
        for i in range(n)
    ]


@pytest.fixture
def win(qtbot, v4_settings):
    return make_window(qtbot)


# ---- mail -------------------------------------------------------------------

def test_hidden_mail_screen_defers_rebuild_on_poll(win, qtbot):
    """A mails_changed signal received while the mail screen is hidden must
    defer the rebuild (set _dirty=True) rather than executing it immediately.

    The causal chain: `_on_mails` checks `isVisible()`, finds False, sets
    `_dirty = True`, and returns without scheduling `rebuild()`. Any other
    path (e.g. calling `rebuild()` anyway) would clear `_dirty` — so
    `_dirty is True` at the end is proof the rebuild was deferred, not run.
    """
    st = win.state
    st.mails = _mails(20)

    win.switch_to("mail")
    mail = win.screen("mail")
    mail.rebuild()              # get into a known, built state

    rebuild_calls = []
    original_rebuild = mail.rebuild
    mail.rebuild = lambda: (rebuild_calls.append(1), original_rebuild())[-1]

    # Move away so the screen is hidden.
    win.switch_to("today")
    assert not mail.isVisible()

    rebuild_calls.clear()
    st.mails = _mails(30)
    st.mails_changed.emit()
    qtbot.wait(50)

    assert mail._dirty is True, (
        "hidden mail screen should have deferred its rebuild with _dirty=True"
    )
    assert not rebuild_calls, (
        "hidden mail screen called rebuild() on a background poll — "
        "this rebuilds potentially hundreds of row widgets for nothing"
    )


def test_hidden_mail_screen_catches_up_when_shown(win, qtbot):
    """Switching back to a dirty mail screen must trigger a rebuild and clear
    the _dirty flag so the rows reflect the latest mail list."""
    st = win.state
    st.mails = _mails(10)

    win.switch_to("mail")
    mail = win.screen("mail")

    win.switch_to("today")
    st.mails = _mails(3)
    st.mails_changed.emit()
    assert mail._dirty is True  # deferred while hidden

    win.switch_to("mail")
    qtbot.wait(50)  # on_shown → rebuild → QTimer.singleShot(0) → rebuild

    assert mail._dirty is False, (
        "on_shown() must clear _dirty by rebuilding (hidden-screen catch-up)"
    )


# ---- todos ------------------------------------------------------------------

def test_hidden_todos_screen_defers_rebuild_on_poll(win, qtbot):
    """A todos_changed signal received while the Todos screen is hidden must
    defer the rebuild (set _dirty=True), not execute it immediately."""
    st = win.state
    st.todos = _todos(20)

    win.switch_to("todos")
    todos = win.screen("todos")
    todos.rebuild()

    rebuild_calls = []
    original_rebuild = todos.rebuild
    todos.rebuild = lambda: (rebuild_calls.append(1), original_rebuild())[-1]

    win.switch_to("today")
    assert not todos.isVisible()

    rebuild_calls.clear()
    st.todos = _todos(5)
    st.todos_changed.emit()
    qtbot.wait(50)

    assert todos._dirty is True, (
        "hidden todos screen should have deferred its rebuild with _dirty=True"
    )
    assert not rebuild_calls, (
        "hidden todos screen called rebuild() on a background poll"
    )


def test_hidden_todos_screen_catches_up_when_shown(win, qtbot):
    """Switching back to a dirty Todos screen must clear _dirty (rebuild ran)."""
    st = win.state
    st.todos = _todos(10)

    win.switch_to("todos")
    todos = win.screen("todos")

    win.switch_to("today")
    st.todos = _todos(4)
    st.todos_changed.emit()
    # After the emit the screen should be dirty (if the deferral works) or
    # already rebuilt. Either way, after switching back it must be False.

    win.switch_to("todos")
    qtbot.wait(50)

    assert todos._dirty is False, (
        "on_shown() must clear _dirty by rebuilding (hidden-screen catch-up)"
    )
