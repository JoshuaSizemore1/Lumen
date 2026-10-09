"""Editing a todo must never look like deleting it (todo-fixes #48).

Josh added the tag "AbellCRM" to a todo and the row vanished. Nothing was
deleted: the list was filtered to a tag, the edit moved the todo out of that
filter, and the screen said nothing about it.
"""
from lumen.ui_v3.screens.todos import TodosScreen
from lumen.ui_v3.state import AppState


def _state():
    st = AppState()
    st.todos = [
        {"id": 1, "text": "Chase the invoice", "done": False, "due_date": None,
         "group": "none", "due": None, "tags": ["billing"], "description": "",
         "source": "ui"},
        {"id": 2, "text": "Book the flight", "done": False, "due_date": None,
         "group": "none", "due": None, "tags": ["billing"], "description": "",
         "source": "ui"},
    ]
    return st


def test_edit_that_leaves_the_active_filter_shows_the_todo_again(qtbot):
    state = _state()
    scr = TodosScreen(state)
    qtbot.addWidget(scr)
    scr._set_tag("billing")
    assert [t["id"] for t in scr._visible()] == [1, 2]

    toasts = []
    state.toast_requested.connect(toasts.append)

    # The edit retags todo 1 — under the old behaviour it simply disappeared.
    state.todos[0]["tags"] = ["abellcrm"]
    scr.note_edited(1)

    assert scr.tag_filter == "all"                    # the filter got out of the way
    assert 1 in [t["id"] for t in scr._visible()]     # and the todo is visible
    assert toasts and "billing" in toasts[0]


def test_edit_inside_the_active_filter_leaves_it_alone(qtbot):
    state = _state()
    scr = TodosScreen(state)
    qtbot.addWidget(scr)
    scr._set_tag("billing")

    state.todos[0]["text"] = "Chase the invoice again"
    scr.note_edited(1)

    assert scr.tag_filter == "billing"      # still matches: nothing to explain


def test_tag_filter_matches_regardless_of_case(qtbot):
    state = _state()
    state.todos[0]["tags"] = ["AbellCRM"]
    state.todos[1]["tags"] = ["abellcrm"]
    scr = TodosScreen(state)
    qtbot.addWidget(scr)
    scr._set_tag("AbellCRM")
    # One spelling, one bucket — a tag typed two ways is still one tag.
    assert [t["id"] for t in scr._visible()] == [1, 2]
