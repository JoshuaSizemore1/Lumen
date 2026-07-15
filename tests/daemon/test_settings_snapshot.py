from lumen.daemon.config import (Config, GoogleConfig, MCPConfig,
                                 MCPServerConfig, SyncConfig)
from lumen.daemon.settings_snapshot import build_settings_snapshot


def test_snapshot_reports_model_and_sync_from_config():
    cfg = Config(model="qwen3:4b-instruct", idle_unload_minutes=10,
                 sync=SyncConfig(gmail_poll_minutes=5, calendar_poll_minutes=5))
    snap = build_settings_snapshot(cfg)
    assert snap["model"]["name"] == "qwen3:4b-instruct"
    assert snap["model"]["runtime"] == "ollama"
    assert snap["model"]["num_ctx"] == 8192
    assert snap["model"]["idle_unload_minutes"] == 10
    assert snap["sync"]["gmail_poll_minutes"] == 5
    assert snap["sync"]["calendar_poll_minutes"] == 5


def test_snapshot_accounts_not_connected_by_default(tmp_path):
    cfg = Config(google=GoogleConfig(token_path=tmp_path / "nope.json"))
    snap = build_settings_snapshot(cfg)
    assert snap["accounts"]["gmail"]["connected"] is False
    assert snap["accounts"]["google_calendar"]["connected"] is False


def test_snapshot_lists_configured_mcp_servers():
    cfg = Config(mcp=MCPConfig(enabled=True, servers=(
        MCPServerConfig(name="search", command="npx", args=("brave-search",)),
    )))
    snap = build_settings_snapshot(cfg)
    assert snap["mcp"]["enabled"] is True
    assert snap["mcp"]["servers"][0]["name"] == "search"
    assert snap["mcp"]["servers"][0]["enabled"] is True
    assert "npx" in snap["mcp"]["servers"][0]["detail"]


def test_snapshot_paths_use_tilde(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = Config(db_path=tmp_path / "share" / "lumen.db",
                 memory_path=tmp_path / "share" / "memory.md")
    snap = build_settings_snapshot(cfg)
    assert snap["paths"]["db"].startswith("~/")
    assert snap["paths"]["memory"].startswith("~/")
