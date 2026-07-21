"""ui_v3 todo detail card (#21) + inline editing (#23).

Clicking a todo opens a card with its description and tags, all editable; Save
writes back through state.update_todo.
"""
from lumen.ui_v3.screens.todos import TodoDetailDialog, TodosScreen
from lumen.ui_v3.state import AppState


def test_detail_dialog_saves_edits(qtbot):
    state = AppState()                      # sample mode
    scr = TodosScreen(state)
    qtbot.addWidget(scr)
    todo = dict(state.todos[0])

    calls = []
    state.update_todo = lambda tid, **kw: calls.append((tid, kw))

    dlg = TodoDetailDialog(scr, state, todo)
    qtbot.addWidget(dlg)
    dlg.text.setText("renamed task")
    dlg.desc.setPlainText("a longer note")
    dlg.tags.setText("admin, urgent")
    dlg.due.setText("2026-08-01")
    dlg._save()

    assert calls, "Save did not call update_todo"
    tid, kw = calls[0]
    assert tid == todo["id"]
    assert kw["text"] == "renamed task"
    assert kw["description"] == "a longer note"
    assert kw["tags"] == ["admin", "urgent"]
    assert kw["due_date"] == "2026-08-01"


def test_detail_dialog_clears_due_when_blank(qtbot):
    state = AppState()
    scr = TodosScreen(state)
    qtbot.addWidget(scr)
    todo = dict(state.todos[0])

    calls = []
    state.update_todo = lambda tid, **kw: calls.append((tid, kw))

    dlg = TodoDetailDialog(scr, state, todo)
    qtbot.addWidget(dlg)
    dlg.due.setText("")
    dlg._save()

    assert calls[0][1]["due_date"] is None
