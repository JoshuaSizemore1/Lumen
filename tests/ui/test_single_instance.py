import pytest
from PyQt6.QtNetwork import QLocalServer

from lumen.ui_v2.single_instance import (
    InstanceAlreadyRunning, InstanceServer, probe, try_send,
)


def test_second_instance_hands_off_command(qtbot):
    name = "lumen-ui-test"
    assert try_send(name, "toggle-launcher") is False   # nobody listening yet
    server = InstanceServer(name)
    with qtbot.waitSignal(server.message, timeout=2000) as blocker:
        assert try_send(name, "toggle-launcher") is True
    assert blocker.args == ["toggle-launcher"]


def test_second_server_refuses_to_steal_a_live_name(qtbot):
    """The bug behind the waybar ghost: the loser of a startup race used to
    removeServer() the winner's name and listen anyway, leaving the winner
    running with a tray icon that no hotkey could ever reach again."""
    name = "lumen-ui-test-steal"
    first = InstanceServer(name)

    with pytest.raises(InstanceAlreadyRunning):
        InstanceServer(name)

    # ...and the first instance still owns the name.
    with qtbot.waitSignal(first.message, timeout=2000) as blocker:
        assert try_send(name, "show") is True
    assert blocker.args == ["show"]


def test_stale_socket_file_is_reclaimed(qtbot):
    """A crashed run leaves the socket file behind; nobody answers it, so the
    next start must take the name rather than refuse to run."""
    name = "lumen-ui-test-stale"
    dead = InstanceServer(name)
    dead._server.close()            # crash: file stays, listener is gone
    assert probe(name) is False

    server = InstanceServer(name)
    with qtbot.waitSignal(server.message, timeout=2000) as blocker:
        assert try_send(name, "show") is True
    assert blocker.args == ["show"]


def test_probe_does_not_deliver_a_command(qtbot):
    """Liveness is a bare connect: a probe that wrote something would raise
    the window (or worse) on the instance it was only meant to detect."""
    name = "lumen-ui-test-probe"
    server = InstanceServer(name)
    seen = []
    server.message.connect(seen.append)

    assert probe(name) is True
    qtbot.wait(300)

    assert seen == []


@pytest.fixture(autouse=True)
def _clean_test_sockets():
    yield
    for suffix in ("", "-steal", "-stale", "-probe"):
        QLocalServer.removeServer("lumen-ui-test" + suffix)
