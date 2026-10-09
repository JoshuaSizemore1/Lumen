"""#61 — "opening the Canvas tab the first time each session shakes the screen".

Profiled, and it was not layout and not Chromium: **146 of the 161 ms of a
CanvasScreen build were a synchronous OS-keyring read on the GUI thread.**
`_connect_hero` called `canvas_creds.load()` to decide whether to show the
"Forget saved login" link, which meant keyring backend discovery plus two
Secret Service round trips over D-Bus — on every content render. A locked
collection makes that block for seconds, or raises a system unlock prompt, in
the middle of a paint.

The keyring is now probed once on a worker thread and the answer cached, so a
render only ever reads a bool. Canvas went from the slowest screen in the app
to among the fastest (142 ms → 25 ms to build, offscreen).
"""
import threading

import pytest

from lumen.ui_v3 import canvas_creds


@pytest.fixture
def fresh_cache(monkeypatch):
    monkeypatch.setattr(canvas_creds, "_has_saved", None, raising=False)
    yield
    monkeypatch.setattr(canvas_creds, "_has_saved", None, raising=False)


def test_has_saved_is_the_default_until_probed(fresh_cache):
    assert canvas_creds.has_saved() is False
    assert canvas_creds.has_saved(default=True) is True


def test_prime_caches_and_never_reprobes(fresh_cache, monkeypatch):
    calls = []
    monkeypatch.setattr(canvas_creds.keyring, "get_password",
                        lambda svc, key: calls.append(key) or "u1234567")
    done = threading.Event()
    canvas_creds.prime(lambda _ok: done.set())
    assert done.wait(2), "prime never reported"
    assert canvas_creds.has_saved() is True
    canvas_creds.prime()
    assert len(calls) == 1, "a second prime went back to the keyring"


def test_save_and_forget_update_the_cache_without_a_reprobe(fresh_cache, monkeypatch):
    monkeypatch.setattr(canvas_creds.keyring, "set_password",
                        lambda *a: None)
    monkeypatch.setattr(canvas_creds.keyring, "delete_password",
                        lambda *a: None)
    assert canvas_creds.save("u1234567", "hunter2") is True
    assert canvas_creds.has_saved() is True
    canvas_creds.forget()
    assert canvas_creds.has_saved() is False


def test_building_the_canvas_screen_never_touches_the_keyring_on_the_gui_thread(
        qtbot, fresh_cache, monkeypatch):
    """The actual #61 invariant. D-Bus may happen — just never here."""
    from lumen.ui_v3.screens.canvas import CanvasScreen
    from lumen.ui_v3.state import AppState

    gui = threading.current_thread()
    offenders = []

    def spy(service, key):
        if threading.current_thread() is gui:
            offenders.append(key)
        return None

    monkeypatch.setattr(canvas_creds.keyring, "get_password", spy)

    screen = CanvasScreen(AppState())      # sample mode, no daemon
    qtbot.addWidget(screen)
    screen._refresh_content()              # the render path that used to block
    screen._refresh_content()
    assert offenders == [], (
        f"synchronous keyring reads on the GUI thread: {offenders}")
