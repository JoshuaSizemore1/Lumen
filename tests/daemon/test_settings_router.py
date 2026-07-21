from lumen.daemon.config import Config
from lumen.daemon.router import Router
from tests.daemon.test_router import FakeLLM, FakeStore, collect


async def test_settings_get_returns_snapshot():
    r = Router(FakeLLM(), FakeStore(), config=Config(model="qwen3:4b-instruct"))
    out = await collect(r, "settings.get", {})
    assert out[0]["result"]["model"]["name"] == "qwen3:4b-instruct"
    assert "accounts" in out[0]["result"] and "sync" in out[0]["result"]


async def test_settings_get_without_config_errs():
    r = Router(FakeLLM(), FakeStore())
    out = await collect(r, "settings.get", {})
    assert "error" in out[0]


async def test_google_reconnect_without_config_errs():
    r = Router(FakeLLM(), FakeStore())
    out = await collect(r, "google.reconnect", {})
    assert "error" in out[0]


async def test_google_reconnect_answers_with_fresh_snapshot(monkeypatch):
    # The route runs the (blocking, browser-driven) consent flow on a thread;
    # stub it out and assert success answers with a settings snapshot so the UI
    # rows flip to 'connected'.
    from lumen.daemon.connectors import google_auth
    monkeypatch.setattr(google_auth, "reconnect", lambda *a, **k: (True, None))
    r = Router(FakeLLM(), FakeStore(), config=Config(model="qwen3:4b-instruct"))
    out = await collect(r, "google.reconnect", {})
    assert "accounts" in out[0]["result"] and out[0]["result"]["model"]["name"]


async def test_google_reconnect_surfaces_failure(monkeypatch):
    from lumen.daemon.connectors import google_auth
    monkeypatch.setattr(google_auth, "reconnect", lambda *a, **k: (False, "nope"))
    r = Router(FakeLLM(), FakeStore(), config=Config(model="qwen3:4b-instruct"))
    out = await collect(r, "google.reconnect", {})
    assert out[0]["error"] == "nope"
