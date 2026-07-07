from PyQt6.QtCore import Qt

from lumen.ui.confirm_dialog import ConfirmDialog


def make(qtbot):
    dlg = ConfirmDialog(
        "Send email", "Lumen will send this message from your connected Gmail account.",
        [("To", "priya.nair@company.com"), ("Subject", "Re: sync interval defaults")],
        "Send email",
    )
    qtbot.addWidget(dlg)
    return dlg


def test_renders_fields_and_warning(qtbot):
    dlg = make(qtbot)
    texts = [lab.text() for lab in dlg.findChildren(type(dlg.title_label))]
    assert "Send email" in texts
    assert any("WRITE ACTION" in t for t in texts)
    assert "priya.nair@company.com" in texts


def test_escape_rejects(qtbot):
    dlg = make(qtbot)
    dlg.show()
    qtbot.keyClick(dlg, Qt.Key.Key_Escape)
    assert dlg.result() == 0


def test_ctrl_return_accepts(qtbot):
    dlg = make(qtbot)
    dlg.show()
    qtbot.keyClick(dlg, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert dlg.result() == 1
