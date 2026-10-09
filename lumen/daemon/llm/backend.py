"""LLMBackend: facade over OllamaClient + ClaudeCliClient, routing by mode.

Mode (off|local|claude) and the chosen Claude model key (haiku|sonnet) are
persisted in a tiny llm_state SQLite table so they survive restarts. embed
always routes to Ollama — Claude has no embeddings API."""

import logging
import sqlite3

from lumen.daemon.llm.client import LLMUnavailable, OllamaClient
from lumen.daemon.llm.claude_cli import ClaudeCliClient

log = logging.getLogger("lumen.daemon")

_CREATE_TABLE = """CREATE TABLE IF NOT EXISTS llm_state
                   (key TEXT PRIMARY KEY, value TEXT NOT NULL)"""

# Human display names for the claude_models map entries.
_CLAUDE_DISPLAY = {"haiku": "Haiku 4.5", "sonnet": "Sonnet 5"}

_DEFAULT_MODE = "local"


class LLMBackend:
    """Route chat/tools to the active backend; embed always goes to Ollama."""

    def __init__(
        self,
        ollama: OllamaClient,
        claude: ClaudeCliClient,
        conn: sqlite3.Connection,
        claude_models: dict | None = None,
        background_pause_at: float = 0.80,
    ):
        self._ollama = ollama
        self._claude = claude
        self._conn = conn
        self._claude_models: dict[str, str] = claude_models or {
            "haiku": "claude-haiku-4-5-20251001",
            "sonnet": "claude-sonnet-5",
        }
        self._pause_at = background_pause_at
        self._init_db()
        # The client was built on the config default; the persisted choice wins,
        # or a restart would show "Sonnet" in Settings while Haiku answered.
        self._claude.model = self._claude_models.get(
            self.claude_model_key, self._claude.model)

    # ── persistence ───────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        with self._conn:
            self._conn.execute(_CREATE_TABLE)
        if self._get("mode") is None:
            # Migration: an existing connection_state.model enabled=0 row means
            # the user had the model turned off — map that to mode "off".
            row = self._conn.execute(
                "SELECT enabled FROM connection_state WHERE name = 'model'"
            ).fetchone()
            initial = "off" if (row is not None and not row[0]) else _DEFAULT_MODE
            self._set("mode", initial)

    def _get(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM llm_state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def _set(self, key: str, value: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO llm_state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value))

    # ── mode / model properties ───────────────────────────────────────────────

    @property
    def mode(self) -> str:
        return self._get("mode") or _DEFAULT_MODE

    @property
    def claude_model_key(self) -> str:
        return self._get("claude_model") or "haiku"

    @property
    def model(self) -> str:
        """Display name of the active model."""
        if self.mode == "claude":
            key = self.claude_model_key
            label = _CLAUDE_DISPLAY.get(key, key.capitalize())
            return f"Claude · {label}"
        return self._ollama.model

    # ── mode control ──────────────────────────────────────────────────────────

    def set_mode(self, mode: str) -> None:
        """Persist the new mode. Caller is responsible for unloading Ollama."""
        if mode not in ("off", "local", "claude"):
            raise ValueError(f"unknown mode {mode!r}")
        prev = self.mode
        if prev not in ("off",):          # remember last active mode
            self._set("last_active_mode", prev)
        self._set("mode", mode)
        # Keep connection_state.model in sync for code that reads it directly.
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO connection_state (name, enabled) VALUES (?, ?) "
                    "ON CONFLICT(name) DO UPDATE SET enabled = excluded.enabled",
                    ("model", 0 if mode == "off" else 1))
        except Exception:
            log.debug("could not sync connection_state.model", exc_info=True)

    def set_mode_enabled(self, enabled: bool) -> None:
        """model.set_enabled alias: false→off, true→restore last non-off mode."""
        if not enabled:
            self.set_mode("off")
        else:
            last = self._get("last_active_mode") or "local"
            self.set_mode(last)

    def set_claude_model(self, key: str) -> None:
        if key not in self._claude_models:
            raise ValueError(f"unknown claude model {key!r}")
        self._set("claude_model", key)
        self._claude.model = self._claude_models[key]

    # ── background gate ───────────────────────────────────────────────────────

    def model_paused(self) -> bool:
        """True when background LLM jobs should not run.

        Off always pauses. Claude pauses when either usage window >= pause_at."""
        if self.mode == "off":
            return True
        if self.mode == "claude":
            usage = self._claude.last_usage
            if usage is None:
                return False
            for window in usage.values():
                if isinstance(window, dict):
                    if window.get("utilization", 0.0) >= self._pause_at:
                        return True
        return False

    # ── state for the router / settings ──────────────────────────────────────

    async def refresh_status(self) -> None:
        """Probe CLI install/login (short-TTL cached in the client) so
        model_state has something to report. Settings calls this; chat never."""
        try:
            await self._claude.status()
        except Exception:
            log.debug("claude status probe failed", exc_info=True)

    def model_state(self) -> dict:
        """Full daemon↔UI contract dict (spec: Daemon ↔ UI contract)."""
        mode = self.mode
        key = self.claude_model_key
        cached = self._claude.last_status or {}
        usage = self._claude.last_usage
        return {
            "enabled": mode != "off",
            "mode": mode,
            "name": self.model,
            "local_name": self._ollama.model,
            "claude_model": key,
            "claude_models": {k: _CLAUDE_DISPLAY.get(k, k) for k in self._claude_models},
            "claude": {
                "installed": cached.get("installed", False),
                "logged_in": cached.get("logged_in"),
                "account": cached.get("account"),
                "usage": usage,
            },
            "runtime": "claude-cli" if mode == "claude" else "ollama",
        }

    # ── LLM interface (delegates to active backend) ───────────────────────────

    @property
    def _active(self):
        return self._claude if self.mode == "claude" else self._ollama

    async def chat(self, messages: list[dict], **kwargs):
        async for chunk in self._active.chat(messages, **kwargs):
            yield chunk

    async def chat_with_tools(self, messages, tools, executor, *,
                               model=None, max_iterations=4, **kwargs):
        async for ev in self._active.chat_with_tools(
                messages, tools, executor,
                model=model, max_iterations=max_iterations, **kwargs):
            yield ev

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        # Always Ollama: Claude has no embeddings API (spec decision 1).
        return await self._ollama.embed(texts, model)

    async def is_loaded(self) -> bool:
        if self.mode == "claude":
            return True
        return await self._ollama.is_loaded()

    async def warm(self, prime: list[dict] | None = None) -> None:
        await self._active.warm(prime)

    async def unload(self) -> None:
        # Always unload Ollama — it's the only resident process.
        await self._ollama.unload()

    async def aclose(self) -> None:
        await self._ollama.aclose()
        await self._claude.aclose()
