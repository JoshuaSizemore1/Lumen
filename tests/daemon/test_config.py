import os
from pathlib import Path

import pytest

from lumen.daemon.config import Config, default_socket_path, load_config


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
