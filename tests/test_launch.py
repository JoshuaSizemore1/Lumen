"""Launcher lifecycle: daemon_alive probing, socket waiting, and the
start-UI-then-stop-only-what-we-started contract."""

import os
import socket
import sys

import pytest

from lumen import launch


class FakeProc:
    def __init__(self, returncode=None):
        self.returncode = returncode
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


def test_daemon_alive_states(tmp_path):
    path = tmp_path / "d.sock"
    assert launch.daemon_alive(path) is False        # nothing there
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(path))
    srv.listen(1)
    assert launch.daemon_alive(path) is True         # live listener
    srv.close()
    assert launch.daemon_alive(path) is False        # stale file refuses connect


def test_wait_for_socket_gives_up_when_daemon_exits(tmp_path):
    assert launch.wait_for_socket(tmp_path / "d.sock", FakeProc(returncode=1),
                                  timeout=5) is False


def test_wait_for_socket_sees_live_socket(tmp_path):
    path = tmp_path / "d.sock"
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(path))
    srv.listen(1)
    try:
        assert launch.wait_for_socket(path, FakeProc(), timeout=5) is True
    finally:
        srv.close()


def test_stop_daemon_terminates():
    proc = FakeProc()
    launch.stop_daemon(proc)
    assert proc.terminated and proc.returncode == 0


def test_stop_daemon_noop_when_already_dead():
    proc = FakeProc(returncode=0)
    launch.stop_daemon(proc)
    assert proc.terminated is False


@pytest.fixture(autouse=True)
def _own_the_lock(monkeypatch, tmp_path):
    """Every main() below is the primary unless it says otherwise, and none of
    them may touch the real runtime lock."""
    monkeypatch.setattr(launch.instance_lock, "lock_path",
                        lambda: tmp_path / "launch.lock")


def test_main_forwards_to_running_instance(monkeypatch):
    sent = []
    monkeypatch.setattr("lumen.ui_v2.single_instance.try_send",
                        lambda name, cmd: sent.append(cmd) or True)
    launch.main()
    assert sent == ["show"]


def test_main_hands_off_to_an_instance_that_is_still_starting(monkeypatch):
    """The waybar-ghost race: `lumen` needs seconds to spawn the daemon and
    import the UI, and answers nothing until then. A second press must wait
    for that instance instead of becoming one."""
    events = []
    attempts = []
    monkeypatch.setattr("lumen.ui_v2.single_instance.try_send",
                        lambda n, c: attempts.append(c) and False
                        or len(attempts) >= 3)     # its server opens on try 3
    monkeypatch.setattr(launch.instance_lock, "acquire", lambda p=None: None)
    monkeypatch.setattr(launch, "spawn_daemon",
                        lambda: events.append("spawn"))
    monkeypatch.setattr("lumen.ui_v3.app.main", lambda: events.append("ui"))

    launch.main()

    assert events == []                      # no rival daemon, no rival tray
    assert attempts == ["show", "show", "show"]


def test_late_hotkey_press_summons_rather_than_toggling(monkeypatch):
    """Pressing super+L again during a slow start means "I'm waiting". Handing
    that over as a toggle would close the launcher the first press opened —
    and, started by the hotkey, quit the app with it."""
    attempts = []
    monkeypatch.setattr("lumen.ui_v2.single_instance.try_send",
                        lambda n, c: attempts.append(c) and False
                        or len(attempts) >= 2)
    monkeypatch.setattr(launch.instance_lock, "acquire", lambda p=None: None)
    monkeypatch.setattr(sys, "argv", ["lumen", "--toggle-launcher"])

    launch.main()

    assert attempts == ["toggle-launcher", "show-launcher"]


def test_handoff_takes_over_when_the_starting_instance_dies(monkeypatch):
    """It crashed mid-startup and dropped the lock — we become the primary
    rather than exiting and leaving the user with nothing."""
    events = []
    grabs = [None, "lock"]
    monkeypatch.setattr("lumen.ui_v2.single_instance.try_send", lambda n, c: False)
    monkeypatch.setattr(launch.instance_lock, "acquire",
                        lambda p=None: grabs.pop(0))
    monkeypatch.setattr(launch.instance_lock, "release", lambda h: None)
    monkeypatch.setattr(launch, "daemon_alive", lambda p: True)
    monkeypatch.setattr("lumen.ui_v3.app.main", lambda: events.append("ui"))

    launch.main()

    assert events == ["ui"]


def test_handoff_gives_up_instead_of_hanging(monkeypatch):
    monkeypatch.setattr("lumen.ui_v2.single_instance.try_send", lambda n, c: False)
    monkeypatch.setattr(launch.instance_lock, "acquire", lambda p=None: None)
    monkeypatch.setattr("lumen.ui_v3.app.main",
                        lambda: (_ for _ in ()).throw(AssertionError("no rival UI")))

    launch.main(handoff_timeout=0.3)          # returns rather than blocking


def test_primary_releases_the_lock_on_exit(monkeypatch):
    released = []
    monkeypatch.setattr("lumen.ui_v2.single_instance.try_send", lambda n, c: False)
    monkeypatch.setattr(launch.instance_lock, "acquire", lambda p=None: "lock")
    monkeypatch.setattr(launch.instance_lock, "release", released.append)
    monkeypatch.setattr(launch, "daemon_alive", lambda p: True)
    monkeypatch.setattr("lumen.ui_v3.app.main", lambda: None)

    launch.main()

    assert released == ["lock"]


# ---- manual kill switch ---------------------------------------------------

def test_quit_asks_the_running_instance_first(monkeypatch, capsys):
    sent = []
    monkeypatch.setattr("lumen.ui_v2.single_instance.try_send",
                        lambda n, c: sent.append(c) or True)
    monkeypatch.setattr(launch.instance_lock, "lumen_pids", lambda: [])
    monkeypatch.setattr(sys, "argv", ["lumen", "--quit"])

    launch.main()

    assert sent == ["quit"]
    assert "quit" in capsys.readouterr().out.lower()


def test_quit_sweeps_processes_no_one_can_talk_to(monkeypatch, capsys):
    """The whole point of the switch: a ghost that owns no socket is exactly
    the thing you cannot otherwise kill."""
    killed = []
    pids = [4242, 4243]
    monkeypatch.setattr("lumen.ui_v2.single_instance.try_send", lambda n, c: False)
    monkeypatch.setattr(launch.instance_lock, "lumen_pids",
                        lambda: pids if not killed else [])
    monkeypatch.setattr(launch.instance_lock, "terminate", killed.extend)
    monkeypatch.setattr(sys, "argv", ["lumen", "--quit"])

    launch.main()

    assert killed == [4242, 4243]
    assert "2" in capsys.readouterr().out


def test_quit_reports_when_nothing_is_running(monkeypatch, capsys):
    monkeypatch.setattr("lumen.ui_v2.single_instance.try_send", lambda n, c: False)
    monkeypatch.setattr(launch.instance_lock, "lumen_pids", lambda: [])
    monkeypatch.setattr(launch.instance_lock, "terminate",
                        lambda pids: (_ for _ in ()).throw(AssertionError("nothing to kill")))
    monkeypatch.setattr(sys, "argv", ["lumen", "--quit"])

    launch.main()

    assert "nothing" in capsys.readouterr().out.lower()


def test_main_starts_ui_then_stops_spawned_daemon(monkeypatch):
    events = []
    proc = FakeProc()
    monkeypatch.setattr("lumen.ui_v2.single_instance.try_send", lambda n, c: False)
    monkeypatch.setattr(launch, "daemon_alive", lambda p: False)
    monkeypatch.setattr(launch, "spawn_daemon",
                        lambda: events.append("spawn") or proc)
    monkeypatch.setattr(launch, "wait_for_socket", lambda p, pr: True)
    monkeypatch.setattr(launch, "stop_daemon",
                        lambda pr: events.append(("stop", pr is proc)))
    monkeypatch.setattr("lumen.ui_v3.app.main", lambda: events.append("ui"))
    monkeypatch.delenv("LUMEN_UNIFIED", raising=False)
    launch.main()
    assert events == ["spawn", "ui", ("stop", True)]
    assert os.environ["LUMEN_UNIFIED"] == "1"


def test_main_stops_daemon_even_when_ui_exits_nonzero(monkeypatch):
    stops = []
    proc = FakeProc()
    monkeypatch.setattr("lumen.ui_v2.single_instance.try_send", lambda n, c: False)
    monkeypatch.setattr(launch, "daemon_alive", lambda p: False)
    monkeypatch.setattr(launch, "spawn_daemon", lambda: proc)
    monkeypatch.setattr(launch, "wait_for_socket", lambda p, pr: True)
    monkeypatch.setattr(launch, "stop_daemon", lambda pr: stops.append(pr))
    monkeypatch.setattr("lumen.ui_v3.app.main",
                        lambda: (_ for _ in ()).throw(SystemExit(3)))
    with pytest.raises(SystemExit):
        launch.main()
    assert stops == [proc]


def test_main_leaves_external_daemon_alone(monkeypatch):
    events = []
    monkeypatch.setattr("lumen.ui_v2.single_instance.try_send", lambda n, c: False)
    monkeypatch.setattr(launch, "daemon_alive", lambda p: True)
    monkeypatch.setattr(launch, "spawn_daemon", lambda: events.append("spawn"))
    monkeypatch.setattr(launch, "stop_daemon", lambda pr: events.append("stop"))
    monkeypatch.setattr("lumen.ui_v3.app.main", lambda: events.append("ui"))
    launch.main()
    assert events == ["ui"]


def test_main_aborts_when_daemon_never_comes_up(monkeypatch):
    stops = []
    proc = FakeProc()
    monkeypatch.setattr("lumen.ui_v2.single_instance.try_send", lambda n, c: False)
    monkeypatch.setattr(launch, "daemon_alive", lambda p: False)
    monkeypatch.setattr(launch, "spawn_daemon", lambda: proc)
    monkeypatch.setattr(launch, "wait_for_socket", lambda p, pr: False)
    monkeypatch.setattr(launch, "stop_daemon", lambda pr: stops.append(pr))
    monkeypatch.setattr("lumen.ui_v3.app.main",
                        lambda: (_ for _ in ()).throw(AssertionError("UI must not start")))
    with pytest.raises(SystemExit, match="did not come up"):
        launch.main()
    assert stops == [proc]
