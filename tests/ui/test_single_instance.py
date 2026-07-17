from lumen.ui_v2.single_instance import InstanceServer, try_send


def test_second_instance_hands_off_command(qtbot):
    name = "lumen-ui-test"
    assert try_send(name, "toggle-launcher") is False   # nobody listening yet
    server = InstanceServer(name)
    with qtbot.waitSignal(server.message, timeout=2000) as blocker:
        assert try_send(name, "toggle-launcher") is True
    assert blocker.args == ["toggle-launcher"]
