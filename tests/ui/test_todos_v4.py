"""v4 Todos fixes: a tag chip filters without opening the edit card (M4), the
#48 "moved out of the filter" notice really fires (M8), a delete inside the
Undo window survives quitting (L6), and the tag filter + Show completed sit
above the list (review.md)."""
import pytest
from PyQt6.QtCore import QPoint, Qt

from lumen.ui_v3.state import AppState
from lumen.ui_v4.components import SwitchRow, TagChip
from tests.ui.v4_support import (  # noqa: F401
    FakeClient, _stub_canvas_creds, make_window, v4_settings,
)


def _row(tid, text, tags=(), done=False, group="none"):
    return {"id": tid, "text": text, "done": done, "tags": list(tags),
            "tag": tags[0] if tags else "", "group": group, "due": "",
            "due_date": None, "description": "", "source": ""}


@pytest.fixture
def sample(qtbot, v4_settings):
    win = make_window(qtbot)
    win.state.todos = [_row(1, "Renew domain", ["admin"]),
                       _row(2, "Call mum", ["home"]),
                       _row(3, "Old thing", ["admin"], done=True)]
    win.switch_to("todos")
    return win, win.screen("todos")


@pytest.fixture
def live(qtbot, v4_settings):
    data = FakeClient()
    win = make_window(qtbot, AppState(data=data))
    win.state._set_todos([])                      # loaded, empty
    return win, data


def test_tag_chip_click_filters_without_opening_the_editor(sample, qtbot):
    win, todos = sample
    chip = next(c for c in todos.list_host.findChildren(TagChip)
                if c.text == "home")
    qtbot.mouseClick(chip, Qt.MouseButton.LeftButton, pos=QPoint(4, 4))
    qtbot.wait(20)
    assert todos.tag_filter == "home"
    assert todos._detail is None or not todos._detail.isVisible()


def test_filters_sit_above_the_list(sample, qtbot):
    win, todos = sample
    chips = [c.text for c in todos.tag_host.findChildren(TagChip)]
    assert chips[0].startswith("All")
    assert any(c.startswith("#admin") for c in chips)
    assert todos.filters_host.findChildren(SwitchRow)
    assert not todos.rail.findChildren(SwitchRow)          # moved out of the rail
    assert todos.filters_host.y() < todos.list_host.y()


def test_show_completed_switch_hides_done(sample, qtbot):
    win, todos = sample
    todos.done_switch.setChecked(False)
    assert not todos.show_done
    assert all(not t["done"] for t in todos._visible())


def test_edit_moving_a_todo_out_of_the_filter_says_so(live, qtbot, monkeypatch):
    """M8: update_todo is async, so the notice has to wait for the store."""
    win, data = live
    win.state._set_todos([{"id": 7, "text": "Renew domain", "completed": False,
                           "tags": ["admin"]}])
    win.switch_to("todos")
    todos = win.screen("todos")
    todos._set_tag("admin")
    toasts = []
    monkeypatch.setattr(win, "show_toast", lambda m, undo=None: toasts.append(m))
    win.state.update_todo(7, tags=["home"])
    todos.note_edited(7)
    assert todos.tag_filter == "admin"            # the store hasn't answered yet
    _payload, cb = data.last("todos.update")
    cb([{"id": 7, "text": "Renew domain", "completed": False, "tags": ["home"]}])
    assert todos.tag_filter == "all"
    assert toasts == ["Moved out of #admin. Showing all todos."]


def test_edit_that_keeps_the_tag_stays_quiet(live, qtbot, monkeypatch):
    win, data = live
    win.state._set_todos([{"id": 7, "text": "Renew", "completed": False,
                           "tags": ["admin"]}])
    win.switch_to("todos")
    todos = win.screen("todos")
    todos._set_tag("admin")
    toasts = []
    monkeypatch.setattr(win, "show_toast", lambda m, undo=None: toasts.append(m))
    win.state.update_todo(7, text="Renew the domain")
    todos.note_edited(7)
    _payload, cb = data.last("todos.update")
    cb([{"id": 7, "text": "Renew the domain", "completed": False,
         "tags": ["admin"]}])
    assert todos.tag_filter == "admin" and toasts == []


def test_pending_delete_is_flushed_on_quit(live, qtbot):
    win, data = live
    win.state._set_todos([{"id": 7, "text": "Renew", "completed": False,
                           "tags": []}])
    win.switch_to("todos")
    todos = win.screen("todos")
    todos.delete_with_undo(win.state.todos[0])
    assert "todos.delete" not in data.request_types()     # waiting out Undo
    todos._flush_deletes()                    # what aboutToQuit runs
    assert data.last("todos.delete")[0] == {"id": 7}
    assert todos._pending_del == {}
