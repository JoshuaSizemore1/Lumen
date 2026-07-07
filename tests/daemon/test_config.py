from pathlib import Path

from lumen.daemon.config import Config, default_socket_path, load_config


def test_defaults_when_no_file(tmp_path):
    cfg = load_config(tmp_path / "nope.toml")
    assert cfg.model == "qwen3:4b"
    assert cfg.idle_unload_minutes == 10
    assert cfg.ollama_url == "http://127.0.0.1:11434"
    assert cfg.socket_path == default_socket_path()


def test_reads_toml(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        '[llm]\nmodel = "gemma3:12b-it-qat"\nidle_unload_minutes = 5\n'
        'ollama_url = "http://127.0.0.1:9999"\n'
        f'[ipc]\nsocket_path = "{tmp_path}/d.sock"\n'
    )
    cfg = load_config(p)
    assert cfg.model == "gemma3:12b-it-qat"
    assert cfg.idle_unload_minutes == 5
    assert cfg.ollama_url == "http://127.0.0.1:9999"
    assert cfg.socket_path == Path(f"{tmp_path}/d.sock")


def test_keep_alive_format():
    assert Config().keep_alive == "10m"
