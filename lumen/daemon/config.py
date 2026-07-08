"""Daemon configuration. Non-secret settings from config.toml; secrets stay in .env."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


def default_socket_path() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base:
        return Path(base) / "lumen" / "daemon.sock"
    return Path(f"/tmp/lumen-{os.getuid()}") / "daemon.sock"


@dataclass(frozen=True)
class Config:
    model: str = "qwen3:4b-instruct"
    idle_unload_minutes: int = 10
    ollama_url: str = "http://127.0.0.1:11434"
    think: bool = False
    socket_path: Path = field(default_factory=default_socket_path)

    @property
    def keep_alive(self) -> str:
        return f"{self.idle_unload_minutes}m"


def load_config(path: Path | None = None) -> Config:
    path = path or Path(__file__).resolve().parent.parent / "config.toml"
    if not path.exists():
        return Config()
    with open(path, "rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise SystemExit(f"lumen: invalid TOML in {path}: {e}")
    llm = data.get("llm", {})
    ipc = data.get("ipc", {})
    kwargs = {}
    if "model" in llm:
        kwargs["model"] = llm["model"]
    if "idle_unload_minutes" in llm:
        kwargs["idle_unload_minutes"] = llm["idle_unload_minutes"]
    if "ollama_url" in llm:
        kwargs["ollama_url"] = llm["ollama_url"]
    if "think" in llm:
        kwargs["think"] = llm["think"]
    if "socket_path" in ipc:
        kwargs["socket_path"] = Path(ipc["socket_path"])
    return Config(**kwargs)
