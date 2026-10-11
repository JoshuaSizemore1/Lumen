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


def test_a_visit_skips_the_folder_the_narrow_editor_hides(files, tree,
                                                          monkeypatch):
    """Review find: every visit re-read the folder and rebuilt its rows on
    the GUI thread, even with the pane hidden behind a narrow editor."""
    win, scr = files
    scr._open(tree / "a.md")
    win.resize(800, 760)
    QApplication.processEvents()
    assert not scr.browser.isVisible()
    reads = []
    real = win.state.list_dir
    monkeypatch.setattr(win.state, "list_dir",
                        lambda *a, **kw: reads.append(a) or real(*a, **kw))
    scr.on_shown()
    assert reads == []
    win.resize(1400, 860)                      # wide again: shown, and fresh
    QApplication.processEvents()
    assert reads and scr.browser.isVisible()


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


# ---- the disk under the editor (todo-fixes #86) ------------------------------
def _write_newer(p, text):
    """An outside write the stamp can't miss, however fast the test runs."""
    import os
    st = p.stat()
    p.write_text(text)
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def test_save_never_writes_stale_text_over_a_newer_file(files, tree):
    """The review's probe: the Ask bar's file tool (or the memory distiller)
    rewrote the open file, and Save — even with no edits — put the old text
    back."""
    win, scr = files
    scr._open(tree / "a.md")
    _write_newer(tree / "a.md", "alpha, from the Ask bar")
    scr._save()
    assert (tree / "a.md").read_text() == "alpha, from the Ask bar"
    assert scr.editor.toPlainText() == "alpha, from the Ask bar"
    assert not scr.dirty


def test_an_unedited_file_follows_the_disk(files, tree, qtbot):
    win, scr = files
    scr._open(tree / "a.md")
    (tree / "a.md").write_text("alpha, rewritten by the distiller")
    # inotify wakes the screen; no polling timer is involved
    qtbot.waitUntil(lambda: scr.editor.toPlainText()
                    == "alpha, rewritten by the distiller", timeout=3000)
    assert not scr.dirty and scr.status_badge.text() == "Saved"


def test_a_change_under_your_edits_asks_and_save_refuses(files, tree):
    win, scr = files
    scr._open(tree / "a.md")
    scr.editor.setPlainText("alpha, mine")
    _write_newer(tree / "a.md", "alpha, theirs")
    assert scr._check_disk() == "conflict"
    assert scr.disk_host.isVisible()
    assert scr.editor.toPlainText() == "alpha, mine" and scr.dirty
    assert scr._save() is False                       # never written blind
    assert (tree / "a.md").read_text() == "alpha, theirs"
    keep = next(b for b in scr.disk_host.findChildren(type(scr.save_btn))
                if b.text() == "Save mine anyway")
    keep.click()
    assert (tree / "a.md").read_text() == "alpha, mine"
    assert not scr.dirty and not scr.disk_host.isVisible()


def test_reload_from_disk_offers_your_edits_back(files, tree, monkeypatch):
    win, scr = files
    undos = []
    monkeypatch.setattr(win, "show_toast",
                        lambda m, undo=None: undos.append(undo))
    scr._open(tree / "a.md")
    scr.editor.setPlainText("alpha, mine")
    _write_newer(tree / "a.md", "alpha, theirs")
    scr._check_disk()
    scr._reload_from_disk()
    assert scr.editor.toPlainText() == "alpha, theirs" and not scr.dirty
    undos[-1]()
    assert scr.editor.toPlainText() == "alpha, mine" and scr.dirty
    assert (tree / "a.md").read_text() == "alpha, theirs"


def test_a_kept_draft_notices_its_file_moved_on(files, tree):
    win, scr = files
    scr._open(tree / "a.md")
    scr.editor.setPlainText("alpha, draft")
    scr._open(tree / "b.txt")                         # a.md becomes a draft
    _write_newer(tree / "a.md", "alpha, newer")
    scr._open(tree / "a.md")
    assert scr.editor.toPlainText() == "alpha, draft" and scr.dirty
    assert scr.disk_host.isVisible()
    assert scr._save() is False
    assert (tree / "a.md").read_text() == "alpha, newer"


# ---- suggested edits (todo-fixes #87) ----------------------------------------
def _suggest(scr, monkeypatch, reply):
    """Ask for an edit and answer it with `reply` (the daemon's content)."""
    asked = []
    monkeypatch.setattr(scr.state, "propose_edit",
                        lambda path, content, instr, cb, on_error=None:
                        asked.append((content, cb)))
    scr.instr.set_text("make it better")
    scr._run_edit()
    content, cb = asked[-1]
    return content, (lambda: cb({"ok": True, "content": reply}))


def _diff_text(scr) -> str:
    from lumen.ui_v4.screens.files import _DiffView
    return scr.prop_host.findChild(_DiffView).toPlainText()


def test_a_suggestion_shows_its_diff_before_anything_changes(files, tree,
                                                             monkeypatch):
    win, scr = files
    (tree / "a.md").write_text("one\ntwo\nthree\n")
    scr._open(tree / "a.md")
    _base, answer = _suggest(scr, monkeypatch, "one\nTWO\nthree\n")
    answer()
    assert scr.prop_host.isVisible()
    diff = _diff_text(scr)
    assert "- two" in diff and "+ TWO" in diff
    assert "1 line added, 1 removed" in scr.prop_note.text()
    assert scr.editor.toPlainText() == "one\ntwo\nthree\n"   # not applied yet


def test_apply_keeps_what_you_typed_while_lumen_drafted(files, tree,
                                                        monkeypatch):
    win, scr = files
    (tree / "a.md").write_text("one\ntwo\nthree\nfour\n")
    scr._open(tree / "a.md")
    _base, answer = _suggest(scr, monkeypatch, "one\nTWO\nthree\nfour\n")
    scr.editor.setPlainText("one\ntwo\nthree\nfour\nfive, typed meanwhile\n")
    answer()
    scr._apply_proposal()
    assert scr.editor.toPlainText() == (
        "one\nTWO\nthree\nfour\nfive, typed meanwhile\n")
    scr.editor.undo()                         # one step takes the apply back
    assert scr.editor.toPlainText() == (
        "one\ntwo\nthree\nfour\nfive, typed meanwhile\n")


def test_apply_refuses_when_you_rewrote_the_same_lines(files, tree,
                                                       monkeypatch):
    win, scr = files
    (tree / "a.md").write_text("one\ntwo\nthree\n")
    scr._open(tree / "a.md")
    _base, answer = _suggest(scr, monkeypatch, "one\nTWO\nthree\n")
    scr.editor.setPlainText("one\ntwo, mine\nthree\n")
    answer()
    scr._apply_proposal()
    assert scr.editor.toPlainText() == "one\ntwo, mine\nthree\n"
    assert scr.prop_host.isVisible() and not scr.apply_btn.isEnabled()


def test_a_reply_that_stops_early_is_flagged(files, tree, monkeypatch):
    win, scr = files
    body = "".join(f"line {i}\n" for i in range(1, 11))
    (tree / "a.md").write_text(body)
    scr._open(tree / "a.md")
    _base, answer = _suggest(scr, monkeypatch,
                             body.replace("line 1\n", "LINE 1\n")[:30])
    answer()
    assert scr.prop_warn.isVisible()
    assert "stopped early" in scr.prop_warn.text()


def test_merge3_cases():
    from lumen.ui_v4.screens.files import merge3
    base = "a\nb\nc\nd\n"
    # separate lines: both kept
    assert merge3(base, "a\nB\nc\nd\n", "a\nb\nc\nD\n") == "a\nB\nc\nD\n"
    # same line: refused
    assert merge3(base, "a\nB1\nc\nd\n", "a\nB2\nc\nd\n") is None
    # identical change on both sides: taken once
    assert merge3(base, "a\nB\nc\nd\n", "a\nB\nc\nd\n") == "a\nB\nc\nd\n"
    # an insert on each side at different spots
    assert merge3(base, "x\na\nb\nc\nd\n", "a\nb\nc\nd\ny\n") == (
        "x\na\nb\nc\nd\ny\n")
    # two different inserts at the same spot: refused
    assert merge3(base, "a\nx\nb\nc\nd\n", "a\ny\nb\nc\nd\n") is None


# ---- quitting with unsaved work (todo-fixes #88) -----------------------------
@pytest.fixture
def quits(monkeypatch):
    calls = []
    monkeypatch.setattr(QApplication.instance(), "quit",
                        lambda: calls.append(1))
    return calls


def _click(win, text):
    from lumen.ui_v4.components import Button
    next(b for b in win.unsaved.card.findChildren(Button)
         if b.text() == text).click()


def test_quit_with_nothing_unsaved_just_quits(files, tree, quits):
    win, scr = files
    scr._open(tree / "a.md")
    win.request_quit()
    assert quits and not win.unsaved.isVisible()


def test_quit_asks_about_the_open_edit_and_a_kept_draft(files, tree, quits):
    win, scr = files
    scr._open(tree / "a.md")
    scr.editor.setPlainText("alpha, unsaved")
    scr._open(tree / "b.txt")                     # a.md is now a kept draft
    scr.editor.setPlainText("bravo, unsaved")
    win.request_quit()
    assert win.unsaved.isVisible() and not quits
    _click(win, "Cancel")
    assert not quits and scr.dirty
    win.request_quit()
    _click(win, "Save and quit")
    assert quits
    assert (tree / "a.md").read_text() == "alpha, unsaved"
    assert (tree / "b.txt").read_text() == "bravo, unsaved"


def test_dont_save_quits_without_writing(files, tree, quits):
    win, scr = files
    scr._open(tree / "a.md")
    scr.editor.setPlainText("alpha, unsaved")
    win.request_quit()
    _click(win, "Don't save")
    assert quits and (tree / "a.md").read_text() == "alpha"


def test_save_and_quit_stays_open_when_the_disk_moved_on(files, tree, quits):
    win, scr = files
    scr._open(tree / "a.md")
    scr.editor.setPlainText("alpha, mine")
    _write_newer(tree / "a.md", "alpha, theirs")
    win.request_quit()
    _click(win, "Save and quit")
    assert not quits
    assert (tree / "a.md").read_text() == "alpha, theirs"
    assert scr.disk_host.isVisible()


def test_the_close_button_in_lumen_mode_asks_first(files, tree, quits):
    win, scr = files
    win.quit_on_close = True
    scr._open(tree / "a.md")
    scr.editor.setPlainText("alpha, unsaved")
    win.close()
    assert win.isVisible() and win.unsaved.isVisible() and not quits


def test_quit_everything_stops_the_daemon_only_once_quitting(files, tree,
                                                             quits,
                                                             monkeypatch):
    from lumen import instance_lock
    win, scr = files
    stopped = []
    monkeypatch.setattr(instance_lock, "daemon_pids", lambda: [123])
    monkeypatch.setattr(instance_lock, "terminate", stopped.append)
    scr._open(tree / "a.md")
    scr.editor.setPlainText("alpha, unsaved")
    win.switch_to("settings")
    win.screen("settings")._quit_everything()
    assert not stopped and not quits
    _click(win, "Cancel")
    assert not stopped
    win.screen("settings")._quit_everything()
    _click(win, "Don't save")
    assert stopped == [[123]] and quits
