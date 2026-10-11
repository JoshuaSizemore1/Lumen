"""Window-level v4 fixes: refresh on reopen from the tray (plan M1), the saved
text size at startup (M3), no model warm on a login-time restore (M5),
history records the view actually shown (L2), screen dialogs block the
shortcuts (L4)."""
from datetime import date

import pytest
from PyQt6.QtWidgets import QApplication

import lumen.ui_v4.theme as T
from lumen.ui_v3.state import AppState
from lumen.ui_v4.styles import build_qss
from tests.ui.v4_support import (  # noqa: F401
    _stub_canvas_creds, make_window, v4_settings,
)


# ---- M3: text size ------------------------------------------------------
def test_saved_text_size_applies_at_startup(qtbot, v4_settings):
    v4_settings().setValue("text_size", 125)
    make_window(qtbot)
    sheet = QApplication.instance().styleSheet()
    assert sheet == T.scale_qss(build_qss(T.current()), 125)
    assert sheet != build_qss(T.current())


def test_text_size_survives_a_theme_switch(qtbot, v4_settings):
    v4_settings().setValue("text_size", 150)
    make_window(qtbot)
    T.manager().set_pref("dark")
    try:
        assert QApplication.instance().styleSheet() == T.scale_qss(
            build_qss(T.current()), 150)
    finally:
        T.manager().set_pref("light")


def test_first_run_carries_over_v3_text_size(qtbot, v4_settings):
    T._v3_settings().setValue("font_scale", 75)
    assert T.text_size() == 75
    assert int(v4_settings().value("text_size")) == 75      # migrated once
    T._v3_settings().setValue("font_scale", 150)
    assert T.text_size() == 75                               # not again


def test_scale_qss_is_identity_at_100():
    assert T.scale_qss("a { font-size: 15px; }", 100) == "a { font-size: 15px; }"
    assert T.scale_qss("a { font-size: 16px; }", 50) == "a { font-size: 8px; }"


# ---- M5: no warm at login ----------------------------------------------
def test_restoring_ask_at_startup_does_not_warm_the_model(qtbot, v4_settings):
    v4_settings().setValue("last_screen", "ask")
    st = AppState()
    calls = []
    st.warm_model = lambda: calls.append(1)
    win = make_window(qtbot, st)
    assert win.current_key() == "ask" and calls == []
    win.switch_to("today")
    win.switch_to("ask")
    assert calls == [1]                     # a user's switch still warms


# ---- M1: refresh when the window comes back ----------------------------
def test_reopening_the_window_refreshes_the_current_screen(qtbot, v4_settings):
    win = make_window(qtbot)
    win.switch_to("todos")
    seen = []
    win.screen("todos").on_shown = lambda **kw: seen.append(kw)
    win.hide()
    win.show()
    assert seen == []                       # just shown: throttled
    win.hide()
    win._last_shown -= win.RESUME_REFRESH_S + 1
    win.show()
    assert seen == [{"resumed": True}]


def test_resume_keeps_the_open_message_in_narrow_mail(qtbot, v4_settings):
    win = make_window(qtbot, size=(800, 760))
    win.switch_to("mail", mid="m2")
    mail = win.screen("mail")
    assert mail._narrow and mail._reading
    win.hide()
    win._last_shown -= win.RESUME_REFRESH_S + 1
    win.show()
    assert mail._reading


# ---- L2: history -------------------------------------------------------
def test_history_records_the_view_switch_to_opened(qtbot, v4_settings):
    win = make_window(qtbot)
    win.switch_to("calendar", view="day", date="2026-10-01")
    entry = win.nav.current
    assert entry.screen == "calendar"
    assert entry.token == ("day", date(2026, 10, 1))


def test_history_records_the_message_switch_to_opened(qtbot, v4_settings):
    win = make_window(qtbot)
    win.switch_to("mail", mid="m2")
    assert win.nav.current.token[1] == "m2"


# ---- L4: screen dialogs are modal ---------------------------------------
def test_shortcuts_wait_behind_a_screen_dialog(qtbot, v4_settings):
    win = make_window(qtbot)
    win.switch_to("todos")
    todos = win.screen("todos")
    todos._open_detail(win.state.todos[0])
    assert win._modal_open()
    win._shortcut_nav("mail")
    assert win.current_key() == "todos"
    todos._detail.close_overlay()
    win._shortcut_nav("mail")
    assert win.current_key() == "mail"


@pytest.mark.parametrize("key", ["ask", "settings"])
def test_no_ask_bar_where_there_is_a_composer(qtbot, v4_settings, key):
    win = make_window(qtbot)
    win.switch_to(key)
    assert not win.askbar.isVisible()


# ---- reduced motion follows the desktop ----------------------------------
@pytest.fixture
def no_env_motion(monkeypatch):
    monkeypatch.delenv("LUMEN_REDUCED_MOTION", raising=False)
    T.system_reduced_motion.cache_clear()
    yield
    T.system_reduced_motion.cache_clear()


def _fake_desktop(monkeypatch, tmp_path, gtk=None, kde=None, hypr=None,
                  gnome=""):
    cfg = tmp_path / "cfg"
    (cfg / "gtk-3.0").mkdir(parents=True)
    if gtk is not None:
        (cfg / "gtk-3.0" / "settings.ini").write_text(
            f"[Settings]\ngtk-enable-animations={gtk}\n")
    if kde is not None:
        (cfg / "kdeglobals").write_text(f"[KDE]\nAnimationDurationFactor={kde}\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    if hypr is None:
        monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
    else:
        monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "x")

    def run(*argv):
        if argv[0] == "hyprctl":
            return f'{{"option": "animations:enabled", "int": {hypr}}}'
        return gnome
    monkeypatch.setattr(T, "_run", run)


@pytest.mark.parametrize("kw,expect", [
    ({}, False),
    ({"gtk": "true", "gnome": "true"}, False),
    ({"gtk": "false"}, True),
    ({"kde": "0"}, True),
    ({"kde": "1.0"}, False),
    ({"hypr": 0}, True),
    ({"hypr": 1, "gnome": "true"}, False),
    ({"gnome": "false"}, True),
])
def test_desktop_animation_setting_is_followed(monkeypatch, tmp_path, v4_settings,
                                               no_env_motion, kw, expect):
    _fake_desktop(monkeypatch, tmp_path, **kw)
    assert T.system_reduced_motion() is expect
    assert T.reduced_motion() is expect


def test_lumens_own_toggle_still_wins(monkeypatch, tmp_path, v4_settings,
                                      no_env_motion):
    _fake_desktop(monkeypatch, tmp_path)
    v4_settings().setValue("reduced_motion", True)
    assert T.reduced_motion() is True
