"""ui_v3 Files screen — its part in the app-wide back/forward history (#29).

Like the calendar, the gesture and history live in the shell (SwipeNavigator +
NavController); the Files screen exposes its in-page location (which folder,
which open file) as a nav token and emits when it navigates so the shell can
record it. Restoring a token must NOT re-emit, or back/forward would pollute
the history it is walking. Per the design choice, every settle is a step:
folder changes AND opening/closing a text file.
"""
from lumen.ui_v3.screens.files import FilesScreen
from lumen.ui_v3.state import AppState


def _screen(qtbot, start):
    scr = FilesScreen(AppState())         # sample mode, real filesystem
    qtbot.addWidget(scr)
    scr.path = start
    scr.rebuild()
    return scr


def test_nav_token_captures_folder_and_open_file(qtbot, tmp_path):
    note = tmp_path / "note.md"
    note.write_text("hi")
    scr = _screen(qtbot, tmp_path)
    assert scr.nav_token() == (tmp_path, None)
    scr._open(note)
    assert scr.nav_token() == (tmp_path, note)


def test_nav_restore_sets_folder(qtbot, tmp_path):
    (tmp_path / "sub").mkdir()
    scr = _screen(qtbot, tmp_path / "sub")
    scr.nav_restore((tmp_path, None))
    assert scr.path == tmp_path
    assert scr.open_file is None


def test_nav_restore_reopens_file_and_rereads_content(qtbot, tmp_path):
    note = tmp_path / "note.md"
    note.write_text("first")
    scr = _screen(qtbot, tmp_path)
    scr.nav_restore((tmp_path, note))
    assert scr.open_file == note
    assert scr.editor_text == "first"


def test_nav_restore_of_deleted_file_falls_back_to_folder(qtbot, tmp_path):
    gone = tmp_path / "gone.md"           # never created
    scr = _screen(qtbot, tmp_path)
    scr.nav_restore((tmp_path, gone))
    assert scr.open_file is None          # no broken editor
    assert scr.path == tmp_path


def test_folder_and_file_navigation_emit_location_changed(qtbot, tmp_path):
    (tmp_path / "sub").mkdir()
    note = tmp_path / "sub" / "note.md"
    note.write_text("hi")
    scr = _screen(qtbot, tmp_path)
    seen = []
    scr.state.nav_location_changed.connect(lambda: seen.append(scr.nav_token()))

    scr._open(tmp_path / "sub")           # enter folder
    scr._open(note)                       # open file
    scr._close()                          # back to folder

    assert seen == [
        (tmp_path / "sub", None),
        (tmp_path / "sub", note),
        (tmp_path / "sub", None),
    ]


def test_reopening_same_folder_does_not_emit(qtbot, tmp_path):
    scr = _screen(qtbot, tmp_path)
    seen = []
    scr.state.nav_location_changed.connect(lambda: seen.append(1))
    scr._open(tmp_path)                   # already here, browsing
    assert seen == []                     # no duplicate history step


def test_restore_does_not_emit(qtbot, tmp_path):
    note = tmp_path / "note.md"
    note.write_text("hi")
    scr = _screen(qtbot, tmp_path)
    seen = []
    scr.state.nav_location_changed.connect(lambda: seen.append(1))
    scr.nav_restore((tmp_path, note))
    assert seen == []                     # restoring is not a new navigation
