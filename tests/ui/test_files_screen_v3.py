"""ui_v3 Files screen — the 'up a directory' button (#25). The breadcrumb
always let you click an ancestor; this adds the one-click step up one level."""
from pathlib import Path

from PyQt6.QtWidgets import QPushButton

from lumen.ui_v3.screens.files import FilesScreen
from lumen.ui_v3.state import AppState


def _up_button(scr) -> QPushButton | None:
    for i in range(scr.crumb_lay.count()):
        w = scr.crumb_lay.itemAt(i).widget()
        if isinstance(w, QPushButton) and "Up" in w.text():
            return w
    return None


def test_up_button_steps_to_parent(qtbot, tmp_path):
    (tmp_path / "alpha").mkdir()
    scr = FilesScreen(AppState())        # sample mode, no daemon
    qtbot.addWidget(scr)

    scr.path = tmp_path / "alpha"
    scr.rebuild()
    up = _up_button(scr)
    assert up is not None and up.isEnabled()

    up.click()
    assert scr.path == tmp_path           # stepped up exactly one level


def test_up_button_disabled_at_filesystem_root(qtbot):
    scr = FilesScreen(AppState())
    qtbot.addWidget(scr)

    root = Path(scr.path.anchor or "/")
    scr.path = root
    scr.rebuild()
    up = _up_button(scr)
    assert up is not None and not up.isEnabled()   # nowhere higher to go


def test_open_path_opens_file_and_lands_in_its_dir(qtbot, tmp_path):
    # Settings' "open memory.md" (#15) routes here instead of an external editor.
    note = tmp_path / "memory.md"
    note.write_text("- learned a thing")
    scr = FilesScreen(AppState())
    qtbot.addWidget(scr)

    scr.open_path(note)

    assert scr.open_file == note          # the file is open in the editor
    assert scr.path == tmp_path           # Close returns to its directory


def test_open_memory_file_emits_signal_not_external(qtbot):
    # ui_v3 keeps it in-app: open_memory_file emits the shell signal (#15).
    state = AppState()
    seen: list[str] = []
    state.open_file_requested.connect(seen.append)

    state.open_memory_file()

    assert len(seen) == 1 and seen[0].endswith("memory.md")


def test_up_button_hidden_while_editing(qtbot, tmp_path):
    # While a file is open the way back is Close, not Up.
    scr = FilesScreen(AppState())
    qtbot.addWidget(scr)
    scr.open_file = tmp_path / "note.md"
    scr.editor_text = "hi"
    scr.rebuild()
    assert _up_button(scr) is None
