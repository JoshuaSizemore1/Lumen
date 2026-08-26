"""Super+L starts Lumen with *only* the launcher overlay — the main window is
never shown, so `quit_on_close` can never fire and dismissing the launcher left
the app (and the daemon the unified launcher spawned for it) resident forever.

Dismissing the launcher in that state must quit; dismissing it while the main
window is up must not. Running a command from the launcher has to bring the
main window up first — otherwise it switches a screen nobody can see, and the
overlay hiding behind it would quit the app instead.
"""
from types import SimpleNamespace

from PyQt6.QtWidgets import QWidget

from lumen.ui_v3.app import install_launcher_quit_policy
from lumen.ui_v3.launcher import COMMANDS, LauncherOverlay, LauncherPalette
from lumen.ui_v3.state import AppState

GO_TO_TODAY = COMMANDS[0]


def _rig(qtbot, unified=True, on_command=None):
    """Overlay + a stand-in main window + a QApplication whose quit we watch."""
    overlay = LauncherOverlay(AppState(), None, on_command=on_command)
    qtbot.addWidget(overlay)
    win = QWidget()
    qtbot.addWidget(win)
    quits = []
    app = SimpleNamespace(quit=lambda: quits.append(1))
    install_launcher_quit_policy(app, overlay, lambda: win, unified=unified)
    return overlay, win, quits


def test_dismissing_hotkey_launcher_quits(qtbot):
    overlay, _win, quits = _rig(qtbot)

    overlay.toggle()                    # super+L: launcher up, main window not
    assert overlay.isVisible()
    overlay.toggle()                    # esc / click away / super+L again

    assert quits == [1]


def test_dismissing_launcher_over_open_window_does_not_quit(qtbot):
    overlay, win, quits = _rig(qtbot)
    win.show()

    overlay.toggle()
    overlay.toggle()

    assert quits == []


def test_no_quit_policy_outside_unified_mode(qtbot):
    """`lumen-ui` against a separately-run daemon: the app outlives the
    launcher on purpose, and the daemon isn't ours to stop."""
    overlay, _win, quits = _rig(qtbot, unified=False)

    overlay.toggle()
    overlay.toggle()

    assert quits == []


def test_launcher_command_presents_main_window_instead_of_quitting(qtbot):
    seen = []
    overlay, win, quits = _rig(qtbot, on_command=lambda: win.show())
    overlay.state.view_requested.connect(seen.append)

    overlay.toggle()
    overlay.palette_widget._run(GO_TO_TODAY)

    assert seen == ["today"]
    assert win.isVisible()
    assert overlay.isVisible() is False
    assert quits == []


def test_summon_is_idempotent_and_never_dismisses(qtbot):
    """The late-handoff path: a second press during a slow start must not
    close the launcher (nor, through it, quit the app).

    The waits matter — re-activating an already-active window posts a
    deactivate, and the overlay dismisses itself on deactivate. Without a
    running event loop that never lands, and the test passes on a bug that
    kills the app within milliseconds in the real one."""
    overlay, _win, quits = _rig(qtbot)

    overlay.summon()
    qtbot.wait(150)
    overlay.summon()
    qtbot.wait(150)

    assert overlay.isVisible()
    assert quits == []


def test_summon_does_not_clear_what_the_user_typed(qtbot):
    overlay, _win, _quits = _rig(qtbot)
    overlay.summon()
    overlay.palette_widget.input.setText("weather tomorrow")

    overlay.summon()

    assert overlay.palette_widget.input.text() == "weather tomorrow"


def test_palette_runs_commands_without_an_on_command_hook(qtbot):
    """The palette is constructed bare in screenshot/sample mode."""
    pal = LauncherPalette(AppState(), None)
    qtbot.addWidget(pal)
    seen = []
    pal.state.view_requested.connect(seen.append)

    pal._run(GO_TO_TODAY)

    assert seen == ["today"]
