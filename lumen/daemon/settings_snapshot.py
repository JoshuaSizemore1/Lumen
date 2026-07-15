"""Read-only snapshot of the loaded Config for the Settings screen (Phase 10).

Pure: derives display values and live account-connection status from an
already-loaded Config. Never touches Ollama, never writes anything."""
from pathlib import Path

from .connectors import google_auth
from .llm.client import NUM_CTX


def _tilde(path) -> str:
    s = str(path)
    home = str(Path.home())
    return "~" + s[len(home):] if s.startswith(home) else s


def _server_detail(server) -> str:
    parts = [server.command, *server.args]
    detail = " ".join(str(p) for p in parts)
    return detail if len(detail) <= 42 else detail[:39] + "…"


def build_settings_snapshot(cfg) -> dict:
    return {
        "model": {
            "runtime": "ollama",
            "name": cfg.model,
            "escalation_model": cfg.escalation_model,
            "num_ctx": NUM_CTX,
            "idle_unload_minutes": cfg.idle_unload_minutes,
            "ollama_url": cfg.ollama_url,
        },
        "sync": {
            "gmail_poll_minutes": cfg.sync.gmail_poll_minutes,
            "calendar_poll_minutes": cfg.sync.calendar_poll_minutes,
            "gmail_window_months": cfg.sync.gmail_window_months,
            "calendar_window_past_days": cfg.sync.calendar_window_past_days,
            "calendar_window_future_days": cfg.sync.calendar_window_future_days,
        },
        "accounts": {
            "gmail": {"connected": google_auth.connected(
                cfg.google, google_auth.GMAIL_READ_SCOPES)},
            "google_calendar": {"connected": google_auth.connected(
                cfg.google, google_auth.READ_SCOPES)},
        },
        "mcp": {
            "enabled": cfg.mcp.enabled,
            "servers": [
                {"name": s.name, "command": s.command,
                 "detail": _server_detail(s), "enabled": cfg.mcp.enabled}
                for s in cfg.mcp.servers
            ],
        },
        "paths": {
            "db": _tilde(cfg.db_path),
            "memory": _tilde(cfg.memory_path),
        },
    }
