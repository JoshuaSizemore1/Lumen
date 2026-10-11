"""v4 Files: hidden files off by default with a switch, list/grid, "N more
not shown", the editor beside the folder (review.md), and unsaved edits never
dropped silently (plan L12). Works in a temp folder only."""
import pytest
from PyQt6.QtWidgets import QApplication

import lumen.ui_v4.theme as T
from lumen.ui_v4.components import ClickRow, ElideLabel, Label
from lumen.ui_v4.screens.files import SIDE_W
from tests.ui.v4_support import (  # noqa: F401
    _stub_canvas_creds, make_window, v4_settings,
)


@pytest.fixture
def tree(tmp_path):
    (tmp_path / ".secret").write_text("x")
    (tmp_path / ".config").mkdir()
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "inner.md").write_text("inner")
    (tmp_path / "a.md").write_text("alpha")
    (tmp_path / "b.txt").write_text("bravo")
    return tmp_path


@pytest.fixture
def files(qtbot, v4_settings, tree):
    win = make_window(qtbot, size=(1400, 860))
    win.switch_to("files")
    scr = win.screen("files")
    scr.open_path(tree)
    return win, scr


def _names(scr) -> set[str]:
    return {lab.text() for lab in scr.browser.findChildren(ElideLabel)}


def test_hidden_files_are_off_by_default(files, tree):
    win, scr = files
    assert ".secret" not in _names(scr) and ".config" not in _names(scr)
    assert {"notes", "a.md", "b.txt"} <= _names(scr)
    scr._set_hidden(True)
    assert {".secret", ".config"} <= _names(scr)
    assert T.settings().value("files/show_hidden", False, bool)


def test_list_and_grid_views(files):
    win, scr = files
    scr._set_view("list")
    assert scr.browser.findChildren(ClickRow)
    scr._set_view("grid")
    assert not scr.browser.findChildren(ClickRow)
    assert T.settings().value("files/view") == "grid"


def test_big_folder_says_how_many_are_not_shown(qtbot, v4_settings, tmp_path):
    big = tmp_path / "big"          # tmp_path also holds the settings file
    big.mkdir()
    for i in range(505):
        (big / f"f{i:03d}.txt").write_text("")
    win = make_window(qtbot)
    win.switch_to("files")
    scr = win.screen("files")
    scr.open_path(big)
    texts = [lab.text() for lab in scr.browser.findChildren(Label)]
    assert any(t.startswith("5 more not shown") for t in texts)


def test_open_file_sits_beside_the_folder(files, tree):
    win, scr = files
    scr._open(tree / "a.md")
    assert scr.editor.toPlainText() == "alpha"
    assert scr.browser.isVisible() and scr.editor_pane.isVisible()
    assert scr.browser.width() == SIDE_W
    win.resize(800, 760)                       # narrow: the editor takes over
    QApplication.processEvents()
    assert not scr.browser.isVisible() and scr.editor_pane.isVisible()


def test_browsing_beside_an_edit_keeps_the_edit(files, tree):
    win, scr = files
    scr._open(tree / "a.md")
    scr.editor.setPlainText("alpha, edited")
    assert scr.dirty
    scr._open(tree / "notes")                  # a folder in the side list
    assert scr.path == tree / "notes"
    assert scr.editor.toPlainText() == "alpha, edited" and scr.dirty


def test_opening_another_file_keeps_the_draft(files, tree, monkeypatch):
    win, scr = files
    toasts = []
    monkeypatch.setattr(win, "show_toast", lambda m, undo=None: toasts.append(m))
    scr._open(tree / "a.md")
    scr.editor.setPlainText("alpha, edited")
    scr._open(tree / "b.txt")
    assert scr.editor.toPlainText() == "bravo" and not scr.dirty
    assert any("Kept your unsaved changes to a.md" in t for t in toasts)
    scr._open(tree / "a.md")
    assert scr.editor.toPlainText() == "alpha, edited"
    assert scr.dirty and scr.status_badge.text() == "Unsaved changes"
    assert (tree / "a.md").read_text() == "alpha"      # nothing was written


def test_back_and_forward_keep_the_draft(files, tree):
    win, scr = files
    scr._open(tree / "a.md")
    scr.editor.setPlainText("alpha, edited")
    win.go_back()                              # to the folder, file closed
    assert scr.open_file is None
    win.go_forward()
    assert scr.open_file == tree / "a.md"
    assert scr.editor.toPlainText() == "alpha, edited" and scr.dirty


def test_history_step_in_the_side_list_keeps_the_edit(files, tree):
    win, scr = files
    scr._open(tree / "a.md")
    scr._open(tree / "notes")
    scr.editor.setPlainText("alpha, edited")
    win.go_back()                              # same file, previous folder
    assert scr.path == tree
    assert scr.editor.toPlainText() == "alpha, edited" and scr.dirty


def test_close_still_offers_undo(files, tree, monkeypatch):
    win, scr = files
    undos = []
    monkeypatch.setattr(win, "show_toast",
                        lambda m, undo=None: undos.append(undo))
    scr._open(tree / "a.md")
    scr.editor.setPlainText("alpha, edited")
    scr._close()
    assert scr.open_file is None and undos[-1] is not None
    undos[-1]()
    assert scr.editor.toPlainText() == "alpha, edited" and scr.dirty


def test_ask_about_this_file_focuses_the_ask_bar(files, tree):
    win, scr = files
    scr._open(tree / "a.md")
    ask = next(b for b in scr.editor_pane.findChildren(type(scr.save_btn))
               if b.text() == "Ask about this file")
    ask.click()
    assert QApplication.focusWidget() is win.askbar.input
    ctx = win.current_ask_context()
    assert ctx["file"] == str(tree / "a.md")


def test_a_failed_open_leaves_the_open_file_alone(files, tree, monkeypatch):
    """Review find: the draft used to be stashed before the new file was
    read, so a failed open left a stale draft that later beat the saved file."""
    win, scr = files
    toasts = []
    monkeypatch.setattr(win, "show_toast", lambda m, undo=None: toasts.append(m))
    bad = tree / "big.log"
    bad.write_bytes(b"\0binary")
    scr._open(tree / "a.md")
    scr.editor.setPlainText("alpha, edited")
    scr._open(bad)
    assert scr.open_file == tree / "a.md" and scr.dirty
    assert scr._drafts == {}
    assert not any("Kept your unsaved" in t for t in toasts)
    scr.editor.setPlainText("alpha, final")
    scr._save()
    scr._close()
    scr._open(tree / "a.md")
    assert scr.editor.toPlainText() == "alpha, final" and not scr.dirty


def test_saving_drops_an_old_draft(files, tree):
    win, scr = files
    scr._drafts[tree / "a.md"] = "stale"
    scr._open(tree / "a.md")
    scr._save()
    assert tree / "a.md" not in scr._drafts
