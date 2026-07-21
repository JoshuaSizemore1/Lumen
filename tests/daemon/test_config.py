import os
from pathlib import Path

import pytest

from lumen.daemon.config import (Config, default_db_path,
                                 default_memory_path, default_procedures_dir,
                                 default_socket_path, load_config)


def test_memory_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert default_memory_path() == tmp_path / "lumen" / "memory.md"
    assert default_procedures_dir() == tmp_path / "lumen" / "procedures"
    c = Config()
    assert c.memory.blob_cap_chars == 4000
    assert c.memory.max_active_procedures == 10


def test_memory_config_overrides(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        "[memory]\n"
        "blob_cap_chars = 3000\n"
        "distill_min_entries = 8\n"
        "max_active_procedures = 5\n"
    )
    c = load_config(p)
    assert c.memory.blob_cap_chars == 3000
    assert c.memory.distill_min_entries == 8
    assert c.memory.max_active_procedures == 5


def test_defaults_when_no_file(tmp_path):
    cfg = load_config(tmp_path / "nope.toml")
    assert cfg.model == "qwen3:4b-instruct"
    assert cfg.idle_unload_minutes == 10
    assert cfg.ollama_url == "http://127.0.0.1:11434"
    assert cfg.think is False


def test_socket_path_uses_xdg_runtime_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert default_socket_path() == tmp_path / "lumen" / "daemon.sock"


def test_socket_path_fallback_without_xdg(monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    assert default_socket_path() == Path(f"/tmp/lumen-{os.getuid()}") / "daemon.sock"


def test_reads_toml(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        '[llm]\nmodel = "gemma3:12b-it-qat"\nidle_unload_minutes = 5\n'
        'ollama_url = "http://127.0.0.1:9999"\nthink = true\n'
        f'[ipc]\nsocket_path = "{tmp_path}/d.sock"\n'
    )
    cfg = load_config(p)
    assert cfg.model == "gemma3:12b-it-qat"
    assert cfg.idle_unload_minutes == 5
    assert cfg.ollama_url == "http://127.0.0.1:9999"
    assert cfg.socket_path == Path(f"{tmp_path}/d.sock")
    assert cfg.think is True


def test_keep_alive_format():
    assert Config().keep_alive == "10m"


def test_malformed_toml_exits_with_clear_message(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("[llm\nmodel = ")
    with pytest.raises(SystemExit, match="invalid TOML"):
        load_config(p)


def test_nonpositive_idle_unload_rejected(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("[llm]\nidle_unload_minutes = -1\n")
    with pytest.raises(SystemExit, match="idle_unload_minutes must be positive"):
        load_config(p)


def test_default_db_path_honors_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert default_db_path() == tmp_path / "lumen" / "lumen.db"


def test_default_db_path_falls_back_to_local_share(monkeypatch):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert default_db_path() == Path.home() / ".local" / "share" / "lumen" / "lumen.db"


def test_db_path_from_toml(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[storage]\ndb_path = "/tmp/x/lumen.db"\n')
    assert load_config(p).db_path == Path("/tmp/x/lumen.db")


def test_db_path_defaults_when_absent(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[llm]\nmodel = "m"\n')
    assert load_config(p).db_path.name == "lumen.db"


from lumen.daemon.config import MCPConfig, MCPServerConfig, default_tool_log_path


def test_mcp_defaults_disabled_when_absent(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[llm]\nmodel = "m"\n')
    cfg = load_config(p)
    assert cfg.mcp.enabled is False
    assert cfg.mcp.servers == ()


def test_mcp_parses_servers_and_allowlist(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        "[mcp]\n"
        "enabled = true\n"
        "max_iterations = 3\n"
        "[[mcp.servers]]\n"
        'name = "fs"\n'
        'command = "npx"\n'
        'args = ["-y", "@modelcontextprotocol/server-filesystem", "~/notes"]\n'
        'tools = ["read_file", "list_directory"]\n'
    )
    cfg = load_config(p)
    assert cfg.mcp.enabled is True
    assert cfg.mcp.max_iterations == 3
    assert cfg.mcp.servers == (
        MCPServerConfig("fs", "npx",
                        ("-y", "@modelcontextprotocol/server-filesystem", "~/notes"),
                        ("read_file", "list_directory")),
    )


def test_mcp_log_path_from_toml(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[mcp]\nenabled = true\nlog_path = "/tmp/x/tool-calls.jsonl"\n')
    assert load_config(p).mcp.log_path == Path("/tmp/x/tool-calls.jsonl")


def test_mcp_server_without_tools_has_none_allowlist(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[mcp]\nenabled = true\n[[mcp.servers]]\nname = "books"\ncommand = "python"\nargs = ["-m", "x"]\n')
    cfg = load_config(p)
    assert cfg.mcp.servers[0].tools is None


def test_default_tool_log_path_honors_xdg_state(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert default_tool_log_path() == tmp_path / "lumen" / "tool-calls.jsonl"


def test_mcp_server_missing_command_exits_friendly(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[mcp]\nenabled = true\n[[mcp.servers]]\nname = "fs"\n')
    with pytest.raises(SystemExit, match="name and command"):
        load_config(p)


def test_mcp_nonpositive_max_iterations_exits(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[mcp]\nenabled = true\nmax_iterations = 0\n')
    with pytest.raises(SystemExit, match="max_iterations"):
        load_config(p)


def test_mcp_write_tools_default_empty(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[mcp]\nenabled = true\n[[mcp.servers]]\nname = "fs"\ncommand = "npx"\n')
    assert load_config(p).mcp.servers[0].write_tools == {}


def test_mcp_write_tools_parsed_as_tuples(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        '[mcp]\nenabled = true\n'
        '[[mcp.servers]]\nname = "fs"\ncommand = "npx"\n'
        'write_tools = { write_file = ["path"], move_file = ["source", "destination"] }\n'
    )
    cfg = load_config(p)
    assert cfg.mcp.servers[0].write_tools == {
        "write_file": ("path",), "move_file": ("source", "destination")}


def test_mcp_write_tool_with_empty_path_args_rejected(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        '[mcp]\nenabled = true\n'
        '[[mcp.servers]]\nname = "fs"\ncommand = "npx"\n'
        'write_tools = { write_file = [] }\n'
    )
    with pytest.raises(SystemExit, match="path argument names"):
        load_config(p)


def test_mcp_write_tool_with_non_list_path_args_rejected(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        '[mcp]\nenabled = true\n'
        '[[mcp.servers]]\nname = "fs"\ncommand = "npx"\n'
        'write_tools = { write_file = "path" }\n'
    )
    with pytest.raises(SystemExit, match="path argument names"):
        load_config(p)


def test_default_grants_path_honors_xdg_data(monkeypatch, tmp_path):
    from lumen.daemon.config import default_grants_path
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert default_grants_path() == tmp_path / "lumen" / "write-grants.txt"


def test_grants_path_from_toml_expands_user(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[mcp]\nenabled = true\ngrants_path = "~/grants.txt"\n')
    assert load_config(p).mcp.grants_path == Path.home() / "grants.txt"


def test_escalation_model_defaults_none(tmp_path):
    assert load_config(tmp_path / "nope.toml").escalation_model is None


def test_escalation_model_from_toml(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[llm]\nescalation_model = "qwen3:14b"\n')
    assert load_config(p).escalation_model == "qwen3:14b"


def test_google_defaults_in_xdg_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    cfg = load_config(tmp_path / "nope.toml")
    assert cfg.google.client_secret_path == tmp_path / "lumen" / "google" / "client_secret.json"
    assert cfg.google.token_path == tmp_path / "lumen" / "google" / "token.json"


def test_google_paths_from_toml_expand_user(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[google]\nclient_secret_path = "~/secrets/cs.json"\n'
                 'token_path = "~/secrets/tok.json"\n')
    cfg = load_config(p)
    assert cfg.google.client_secret_path == Path.home() / "secrets" / "cs.json"
    assert cfg.google.token_path == Path.home() / "secrets" / "tok.json"


def test_sync_defaults(tmp_path):
    cfg = load_config(tmp_path / "nope.toml")
    assert cfg.sync.calendar_poll_minutes == 5
    assert cfg.sync.calendar_window_past_days == 30
    assert cfg.sync.calendar_window_future_days == 60


def test_sync_overrides_from_toml(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("[sync]\ncalendar_poll_minutes = 15\n"
                 "calendar_window_past_days = 7\ncalendar_window_future_days = 90\n")
    cfg = load_config(p)
    assert cfg.sync.calendar_poll_minutes == 15
    assert cfg.sync.calendar_window_past_days == 7
    assert cfg.sync.calendar_window_future_days == 90


def test_sync_poll_tighter_than_five_minutes_rejected(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("[sync]\ncalendar_poll_minutes = 4\n")
    with pytest.raises(SystemExit, match="poll minutes must be at least 5"):
        load_config(p)


def test_sync_negative_window_rejected(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("[sync]\ncalendar_window_past_days = -1\n")
    with pytest.raises(SystemExit, match="window"):
        load_config(p)


def test_sync_gmail_defaults_and_parse(tmp_path):
    cfg = load_config(tmp_path / "missing.toml")
    assert cfg.sync.gmail_poll_minutes == 5
    assert cfg.sync.gmail_window_months == 6
    p = tmp_path / "c.toml"
    p.write_text("[sync]\ngmail_poll_minutes = 7\ngmail_window_months = 12\n")
    cfg = load_config(p)
    assert cfg.sync.gmail_poll_minutes == 7
    assert cfg.sync.gmail_window_months == 12


def test_sync_gmail_rejects_tight_poll_and_bad_window(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("[sync]\ngmail_poll_minutes = 1\n")
    with pytest.raises(SystemExit):
        load_config(p)
    p.write_text("[sync]\ngmail_window_months = 0\n")
    with pytest.raises(SystemExit):
        load_config(p)


def test_scheduling_section_parses_and_validates(tmp_path):
    from lumen.daemon.config import load_config
    p = tmp_path / "config.toml"
    p.write_text('[scheduling]\nday_start = "09:00"\nday_end = "17:30"\n')
    cfg = load_config(p)
    assert cfg.scheduling.day_start == "09:00"
    assert cfg.scheduling.day_end == "17:30"
    # defaults when the section is absent
    (tmp_path / "empty.toml").write_text("")
    d = load_config(tmp_path / "empty.toml")
    assert d.scheduling.day_start == "08:00" and d.scheduling.day_end == "20:00"


def test_scheduling_rejects_bad_hours(tmp_path):
    import pytest
    from lumen.daemon.config import load_config
    p = tmp_path / "config.toml"
    p.write_text('[scheduling]\nday_start = "20:00"\nday_end = "08:00"\n')
    with pytest.raises(SystemExit):
        load_config(p)
    p.write_text('[scheduling]\nday_start = "8am"\n')
    with pytest.raises(SystemExit):
        load_config(p)


def test_canvas_defaults_when_section_absent(tmp_path):
    cfg = load_config(tmp_path / "nope.toml")
    assert cfg.canvas.enabled is False
    assert cfg.canvas.poll_minutes == 45
    assert cfg.canvas.base_url == "https://utah.instructure.com"


def test_canvas_section_parsed_and_base_url_trimmed(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("[canvas]\nenabled = true\npoll_minutes = 30\n"
                 "base_url = 'https://utah.instructure.com/'\n")
    cfg = load_config(p)
    assert cfg.canvas.enabled is True
    assert cfg.canvas.poll_minutes == 30
    assert cfg.canvas.base_url == "https://utah.instructure.com"


def test_canvas_poll_floor_rejected(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("[canvas]\npoll_minutes = 2\n")
    with pytest.raises(SystemExit, match="at least 5"):
        load_config(p)
