"""#47 — "stray daemons need a way to be killed".

The daemon deliberately outlives the window (it is what keeps mail, calendar
and Canvas in sync), which is exactly how one ends up running with no window
left to reach it from. `lumen --quit` swept everything from a terminal; this
adds the two narrower paths: `lumen-daemon --stop` for the daemon alone, and a
Settings section for when there is no terminal open.
"""
from lumen import instance_lock


def test_daemon_argv_matching_is_narrow():
    assert instance_lock.is_daemon_argv(["/opt/venv/bin/lumen-daemon"]) is True
    assert instance_lock.is_daemon_argv(
        ["/usr/bin/python3", "/opt/venv/bin/lumen-daemon"]) is True
    assert instance_lock.is_daemon_argv(
        ["/usr/bin/python3", "-m", "lumen.daemon"]) is True
    # the UI is not a daemon — stopping sync must not kill the window
    assert instance_lock.is_daemon_argv(["/opt/venv/bin/lumen-ui"]) is False
    assert instance_lock.is_daemon_argv(["/opt/venv/bin/lumen"]) is False
    assert instance_lock.is_daemon_argv(
        ["/usr/bin/python3", "-m", "lumen.ui_v3"]) is False
    assert instance_lock.is_daemon_argv([]) is False


def test_daemon_pids_are_a_subset_of_lumen_pids(monkeypatch):
    procs = {
        11: ["/opt/venv/bin/lumen-daemon"],
        12: ["/opt/venv/bin/lumen-ui"],
        13: ["/usr/bin/python3", "-m", "lumen.daemon"],
        14: ["/usr/bin/vim", "lumen/daemon/router.py"],
    }
    monkeypatch.setattr(instance_lock, "_pids_matching",
                        lambda pred: sorted(p for p, a in procs.items() if pred(a)))
    assert instance_lock.daemon_pids() == [11, 13]
    assert instance_lock.lumen_pids() == [11, 12, 13]


def test_daemon_stop_reports_and_terminates(monkeypatch, capsys):
    from lumen.daemon import __main__ as dm
    killed = []
    monkeypatch.setattr(instance_lock, "daemon_pids", lambda: [42, 43])
    monkeypatch.setattr(instance_lock, "terminate",
                        lambda pids, **kw: killed.extend(pids))
    assert dm.stop() == 0
    assert killed == [42, 43]
    assert "stopped 2 daemon" in capsys.readouterr().out


def test_daemon_stop_says_so_when_nothing_is_running(monkeypatch, capsys):
    from lumen.daemon import __main__ as dm
    monkeypatch.setattr(instance_lock, "daemon_pids", lambda: [])
    monkeypatch.setattr(instance_lock, "terminate",
                        lambda *a, **k: pytest_fail())
    assert dm.stop() == 0
    assert "nothing running" in capsys.readouterr().out


def pytest_fail():
    raise AssertionError("terminate() called with no daemons")


def test_settings_stop_button_sweeps_only_daemons(qtbot, monkeypatch):
    from lumen.ui_v3.screens.settings import SettingsScreen
    from lumen.ui_v3.state import AppState

    killed = []
    monkeypatch.setattr(instance_lock, "daemon_pids", lambda: [7])
    monkeypatch.setattr(instance_lock, "terminate",
                        lambda pids, **kw: killed.extend(pids))
    st = AppState()
    screen = SettingsScreen(st)
    qtbot.addWidget(screen)
    toasts = []
    st.toast_requested.connect(toasts.append)

    screen._stop_daemons()
    assert killed == [7]
    assert toasts and "Stopped background sync" in toasts[0]


def test_settings_shows_whether_the_daemon_is_running(qtbot, monkeypatch):
    from lumen.ui_v3.screens.settings import SettingsScreen
    from lumen.ui_v3.state import AppState

    monkeypatch.setattr(instance_lock, "daemon_pids", lambda: [])
    screen = SettingsScreen(AppState())
    qtbot.addWidget(screen)
    screen.rebuild()
    texts = _all_text(screen)
    assert any("not running" in t for t in texts), texts[:20]

    monkeypatch.setattr(instance_lock, "daemon_pids", lambda: [1, 2])
    screen.rebuild()
    assert any("2 running" in t for t in _all_text(screen))


def _all_text(w):
    from PyQt6.QtWidgets import QLabel
    return [c.text() for c in w.findChildren(QLabel)]
