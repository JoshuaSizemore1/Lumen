"""Launcher lifecycle: daemon_alive probing, socket waiting, and the
start-UI-then-stop-only-what-we-started contract."""

import os
import socket

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


def test_main_forwards_to_running_instance(monkeypatch):
    sent = []
    monkeypatch.setattr("lumen.ui_v2.single_instance.try_send",
                        lambda name, cmd: sent.append(cmd) or True)
    launch.main()
    assert sent == ["show"]


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
