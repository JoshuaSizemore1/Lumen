"""Files workbench screen (new-features items 6-7): browsing, the editor's
dirty/save cycle, honest placeholders, asks carrying cwd/open_file over the
chat op, and the propose → diff → apply/discard edit flow."""

from lumen.ui_v2.state import AppState
from lumen.ui_v2.screens.files import FilesScreen

from tests.ui.test_ui_v2 import FakeClient


def _screen(qtbot, chat=None, live=False):
    if live:
        data = FakeClient()
        state = AppState(data=data, chat=chat, confirm=FakeClient())
    else:
        data = None
        state = AppState()
    scr = FilesScreen(state, chat_client=chat)
    qtbot.addWidget(scr)
    return scr, state, data


def _row_names(scr):
    names = []
    for i in range(scr.rows_lay.count()):
        w = scr.rows_lay.itemAt(i).widget()
        if w is not None and w.layout() is not None:
            names.append(w.layout().itemAt(0).widget().text())
    return names


# ---- browsing ------------------------------------------------------------

def test_browse_lists_dirs_first_and_navigates(qtbot, tmp_path):
    (tmp_path / "b.txt").write_text("x")
    sub = tmp_path / "alpha"
    sub.mkdir()
    (sub / "inner.md").write_text("y")
    scr, _s, _d = _screen(qtbot)
    scr.navigate(tmp_path)
    assert scr.cwd == str(tmp_path)
    assert scr.path_box.text() == str(tmp_path)
    assert _row_names(scr) == ["alpha/", "b.txt"]
    assert "2 items" in scr.dir_status.text()

    scr.navigate(sub)                      # click-a-folder equivalent
    assert _row_names(scr) == ["inner.md"]
    scr._go_up()
    assert scr.cwd == str(tmp_path)


def test_browse_error_and_empty_are_messages_not_crashes(qtbot, tmp_path):
    scr, _s, _d = _screen(qtbot)
    scr.navigate(tmp_path / "missing")
    assert scr.dir_status.text()           # the OS error, surfaced
    scr.navigate(tmp_path)
    assert "0 items" in scr.dir_status.text()


# ---- editor --------------------------------------------------------------

def test_open_edit_save_roundtrip(qtbot, tmp_path):
    f = tmp_path / "note.md"
    f.write_text("original")
    scr, state, _d = _screen(qtbot)
    scr.navigate(tmp_path)
    scr.open_file(str(f))
    assert scr.editor.toPlainText() == "original"
    assert scr._dirty is False and not scr.save_btn.isEnabled()

    scr.editor.setPlainText("changed")
    assert scr._dirty is True and scr.save_btn.isEnabled()

    toasts = []
    state.toast_requested.connect(toasts.append)
    scr._save()
    assert f.read_text() == "changed"
    assert scr._dirty is False
    assert any("Saved" in t for t in toasts)


def test_binary_and_oversized_files_get_placeholders(qtbot, tmp_path):
    blob = tmp_path / "blob.bin"
    blob.write_bytes(b"\x00\x01")
    scr, _s, _d = _screen(qtbot)
    scr.navigate(tmp_path)
    scr.open_file(str(blob))
    assert scr.open_path is None           # not editable
    assert not scr.edit_btn.isEnabled() and not scr.save_btn.isEnabled()


def test_opening_another_file_replaces_buffer_cleanly(qtbot, tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("aaa")
    b.write_text("bbb")
    scr, _s, _d = _screen(qtbot)
    scr.navigate(tmp_path)
    scr.open_file(str(a))
    scr.open_file(str(b))
    assert scr.editor.toPlainText() == "bbb"
    assert scr._dirty is False             # loading is not a user edit


# ---- asks ----------------------------------------------------------------

def test_ask_sends_chat_with_cwd_and_open_file(qtbot, tmp_path):
    (tmp_path / "x.md").write_text("x")
    chat = FakeClient()
    scr, _s, _d = _screen(qtbot, chat=chat, live=True)
    scr.navigate(tmp_path)
    scr.open_file(str(tmp_path / "x.md"))
    scr.input.setText("what is this?")
    scr._ask()
    type_, payload = chat.sent[-1]
    assert type_ == "chat"
    assert payload["cwd"] == str(tmp_path)
    assert payload["open_file"] == str(tmp_path / "x.md")
    assert "conversation_id" not in payload   # first ask starts the thread

    chat.conversation.emit(42)
    chat.chunk.emit("an ")
    chat.chunk.emit("answer")
    chat.done.emit()
    assert scr.answer_lab.text() == "an answer"
    assert scr._busy is False

    scr.input.setText("and this?")            # follow-up continues the thread
    scr._ask()
    assert chat.sent[-1][1]["conversation_id"] == 42


def test_ask_without_open_file_omits_the_key(qtbot, tmp_path):
    chat = FakeClient()
    scr, _s, _d = _screen(qtbot, chat=chat, live=True)
    scr.navigate(tmp_path)
    scr.input.setText("what's here?")
    scr._ask()
    assert "open_file" not in chat.sent[-1][1]


def test_ask_error_lands_in_answer_panel(qtbot, tmp_path):
    chat = FakeClient()
    scr, _s, _d = _screen(qtbot, chat=chat, live=True)
    scr.navigate(tmp_path)
    scr.input.setText("q")
    scr._ask()
    chat.error.emit("daemon offline")
    assert scr.answer_lab.text() == "daemon offline"
    assert scr._busy is False


def test_sample_mode_ask_is_honest(qtbot, tmp_path):
    scr, _s, _d = _screen(qtbot)
    scr.navigate(tmp_path)
    scr.input.setText("q")
    scr._ask()
    assert "Sample mode" in scr.answer_lab.text()


# ---- assisted edits ------------------------------------------------------

def test_edit_proposal_diff_apply_writes_only_on_apply(qtbot, tmp_path):
    f = tmp_path / "doc.md"
    f.write_text("one\ntwo\n")
    scr, state, data = _screen(qtbot, live=True)
    scr.navigate(tmp_path)
    scr.open_file(str(f))
    scr.input.setText("uppercase the second line")
    scr._request_edit()

    type_, payload, cb = data.requests[-1]
    assert type_ == "files.propose_edit"
    assert payload["path"] == str(f)
    assert payload["content"] == "one\ntwo\n"
    assert payload["instruction"] == "uppercase the second line"
    assert f.read_text() == "one\ntwo\n"      # nothing written yet

    cb({"ok": True, "content": "one\nTWO\n"})
    assert not scr.diff_host.isHidden() and scr.editor.isHidden()
    assert scr._proposal == "one\nTWO\n"
    assert f.read_text() == "one\ntwo\n"      # still nothing written

    scr._apply_proposal()
    assert f.read_text() == "one\nTWO\n"
    assert scr._proposal is None
    assert scr.editor.toPlainText() == "one\nTWO\n"
    assert scr._dirty is False


def test_edit_proposal_discard_writes_nothing(qtbot, tmp_path):
    f = tmp_path / "doc.md"
    f.write_text("keep me")
    scr, _state, data = _screen(qtbot, live=True)
    scr.navigate(tmp_path)
    scr.open_file(str(f))
    scr.input.setText("rewrite it")
    scr._request_edit()
    data.requests[-1][2]({"ok": True, "content": "rewritten"})
    scr._discard_proposal()
    assert f.read_text() == "keep me"
    assert scr._proposal is None
    assert scr.edit_btn.isEnabled()


def test_edit_failure_is_a_toast_not_a_diff(qtbot, tmp_path):
    f = tmp_path / "doc.md"
    f.write_text("body")
    scr, state, data = _screen(qtbot, live=True)
    scr.navigate(tmp_path)
    scr.open_file(str(f))
    toasts = []
    state.toast_requested.connect(toasts.append)
    scr.input.setText("change")
    scr._request_edit()
    data.requests[-1][2]({"ok": False, "message": "The model proposed no changes."})
    assert scr._proposal is None
    assert any("no changes" in t for t in toasts)
    assert scr.edit_btn.isEnabled()


def test_edit_needs_an_instruction(qtbot, tmp_path):
    f = tmp_path / "doc.md"
    f.write_text("body")
    scr, state, data = _screen(qtbot, live=True)
    scr.navigate(tmp_path)
    scr.open_file(str(f))
    toasts = []
    state.toast_requested.connect(toasts.append)
    scr.input.setText("   ")
    scr._request_edit()
    assert not any(t == "files.propose_edit" for t, _p, _cb in data.requests)
    assert toasts                              # the hint, not a silent no-op
