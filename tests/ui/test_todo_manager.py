from datetime import date

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QLabel

from lumen.ui.todo_manager import TodoScreen, format_due, group_todos

TODAY = date(2026, 7, 8)  # a Wednesday


def row(id=1, text="x", due=None, completed=False, tags=(), created="2026-07-08T10:00:00"):
    return {"id": id, "text": text, "due_date": due, "completed": completed,
            "created_at": created, "source": "manual", "tags": list(tags)}


class FakeClient(QObject):
    error = pyqtSignal(str)

    def __init__(self, rows=None):
        super().__init__()
        self.rows = rows if rows is not None else []
        self.requests: list[tuple[str, dict]] = []

    def request(self, type_, payload, on_result):
        self.requests.append((type_, payload))
        on_result(self.rows)


def make_screen(qtbot, rows=None):
    client = FakeClient(rows)
    screen = TodoScreen(client)
    qtbot.addWidget(screen)
    screen.show()  # fires showEvent -> initial todos.list
    return screen, client


def texts(widget) -> str:
    return " | ".join(lab.text() for lab in widget.findChildren(QLabel))


def test_group_todos_buckets_and_orders():
    rows = [
        row(1, "overdue", "2026-07-01"),
        row(2, "later", "2026-07-20"),
        row(3, "someday"),
        row(4, "done today", "2026-07-08", completed=True),
        row(5, "due today", "2026-07-08"),
    ]
    groups = {name: [t["id"] for t in items] for name, items in group_todos(rows, TODAY)}
    assert groups["TODAY"] == [1, 5, 4]  # open by due date, completed last
    assert groups["UPCOMING"] == [2]
    assert groups["NO DATE"] == [3]


def test_group_todos_skips_empty_groups():
    assert [name for name, _ in group_todos([row(1)], TODAY)] == ["NO DATE"]


def test_format_due():
    assert format_due("2026-07-08", TODAY) == "today"
    assert format_due("2026-07-11", TODAY) == "Sat"     # within 6 days -> weekday
    assert format_due("2026-07-20", TODAY) == "Jul 20"  # farther out -> absolute
    assert format_due("2026-07-01", TODAY) == "Jul 1"   # overdue -> absolute


def test_loads_renders_and_counts(qtbot):
    today_iso = date.today().isoformat()
    screen, client = make_screen(qtbot, [
        row(1, "call dentist", today_iso, tags=("personal",)),
        row(2, "water plants", completed=True),
    ])
    assert client.requests[0] == ("todos.list", {})
    t = texts(screen)
    assert "TODAY" in t and "call dentist" in t and "personal" in t
    assert "1 open" in t


def test_empty_list_shows_empty_state(qtbot):
    screen, _ = make_screen(qtbot, [])
    assert "no todos yet" in texts(screen)


def test_add_sends_raw_text_and_clears_field(qtbot):
    screen, client = make_screen(qtbot)
    screen.field.setText("renew domain @jul9 #admin")
    screen._add()
    assert ("todos.add", {"text": "renew domain @jul9 #admin"}) in client.requests
    assert screen.field.text() == ""


def test_empty_add_is_noop(qtbot):
    screen, client = make_screen(qtbot)
    screen.field.setText("   ")
    screen._add()
    assert all(t != "todos.add" for t, _ in client.requests)


def test_toggle_sends_request(qtbot):
    screen, client = make_screen(qtbot, [row(7, "x")])
    screen.findChild(QCheckBox).setChecked(True)
    assert ("todos.toggle", {"id": 7, "completed": True}) in client.requests


def test_delete_sends_request(qtbot):
    screen, client = make_screen(qtbot, [row(7, "x")])
    x = next(lab for lab in screen.findChildren(QLabel) if lab.text() == "✕")
    x.clicked.emit()
    assert ("todos.delete", {"id": 7}) in client.requests


def test_error_shows_banner_and_next_result_clears_it(qtbot):
    screen, client = make_screen(qtbot)
    client.error.emit("daemon offline — start it")
    assert screen.status.isVisible() and "offline" in screen.status.text()
    screen._set_todos([])
    assert not screen.status.isVisible()


def test_unknown_tag_renders_with_fallback_color(qtbot):
    screen, _ = make_screen(qtbot, [row(1, "x", tags=("zebra",))])
    assert "zebra" in texts(screen)  # no KeyError on unknown tag


def test_delete_ignores_non_left_buttons(qtbot):
    screen, client = make_screen(qtbot, [row(7, "x")])
    x = next(lab for lab in screen.findChildren(QLabel) if lab.text() == "✕")
    qtbot.mouseClick(x, Qt.MouseButton.RightButton)
    assert ("todos.delete", {"id": 7}) not in client.requests
    qtbot.mouseClick(x, Qt.MouseButton.LeftButton)
    assert ("todos.delete", {"id": 7}) in client.requests
