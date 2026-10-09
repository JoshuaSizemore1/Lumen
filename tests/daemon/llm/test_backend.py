"""Tests for LLMBackend: mode routing, persistence, Ollama-only paths,
backoff predicate, and the key test: in claude mode an OllamaDouble that
fails on any call except embed verifies the local model never loads."""

import sqlite3
from pathlib import Path

import pytest

from lumen.daemon import db
from lumen.daemon.llm.backend import LLMBackend
from lumen.daemon.llm.client import LLMUnavailable
from lumen.daemon.llm.claude_cli import ClaudeUnavailable


# ── stubs ──────────────────────────────────────────────────────────────────────

class OllamaDouble:
    """Fails on every call except embed. Used to prove Ollama never loads."""
    def __init__(self, model="qwen3:4b-instruct", fail_all=False):
        self.model = model
        self.fail_all = fail_all
        self.unloaded = False
        self.warmed = False
        self.calls: list[str] = []

    async def chat(self, messages, **kwargs):
        self.calls.append("chat")
        if self.fail_all:
            raise AssertionError("OllamaDouble.chat() called — local model must NOT load in Claude mode")
        for _ in []:
            yield ""  # pragma: no cover

    async def chat_with_tools(self, messages, tools, executor, **kwargs):
        self.calls.append("chat_with_tools")
        if self.fail_all:
            raise AssertionError("OllamaDouble.chat_with_tools() called — must not load in Claude mode")
        for _ in []:
            yield {}  # pragma: no cover

    async def embed(self, texts, model):
        self.calls.append("embed")
        return [[0.1] * 3 for _ in texts]

    async def is_loaded(self):
        return False

    async def warm(self, prime=None):
        self.warmed = True

    async def unload(self):
        self.unloaded = True

    async def aclose(self):
        pass


class ClaudeDouble:
    """Minimal ClaudeCliClient stub."""
    def __init__(self, model="claude-haiku-4-5-20251001",
                 chunks=("Hello",), fail=False, fail_with=None):
        self.model = model
        self._chunks = chunks
        self._fail = fail
        self._fail_with = fail_with
        self.last_usage: dict | None = None
        self._auth_cache = None
        self.calls: list[str] = []

    @property
    def last_status(self):
        return self._auth_cache[1] if self._auth_cache else None

    async def chat(self, messages, **kwargs):
        self.calls.append("chat")
        if self._fail:
            raise (self._fail_with or ClaudeUnavailable("down", reason="offline"))
        for c in self._chunks:
            yield c

    async def chat_with_tools(self, messages, tools, executor, **kwargs):
        self.calls.append("chat_with_tools")
        if self._fail:
            raise (self._fail_with or ClaudeUnavailable("down", reason="offline"))
        yield {"content": "answer"}

    async def is_loaded(self):
        return True

    async def warm(self, prime=None):
        pass

    async def unload(self):
        pass

    async def aclose(self):
        pass


def make_db(tmp_path: Path) -> sqlite3.Connection:
    conn = db.connect(tmp_path / "test.db")
    # Ensure connection_state table exists (normally created by db module)
    conn.execute("""CREATE TABLE IF NOT EXISTS connection_state
                    (name TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1)""")
    return conn


def make_backend(tmp_path: Path, *, mode: str = "local",
                 fail_all_ollama: bool = False,
                 claude_chunks=("Hi",)) -> tuple[LLMBackend, OllamaDouble, ClaudeDouble]:
    conn = make_db(tmp_path)
    ollama = OllamaDouble(fail_all=fail_all_ollama)
    claude = ClaudeDouble(chunks=claude_chunks)
    backend = LLMBackend(ollama, claude, conn)
    backend.set_mode(mode)
    return backend, ollama, claude


# ── mode routing ───────────────────────────────────────────────────────────────

async def test_local_mode_routes_chat_to_ollama(tmp_path):
    backend, ollama, claude = make_backend(tmp_path, mode="local")
    ollama.fail_all = False
    chunks = [c async for c in backend.chat([{"role": "user", "content": "hi"}])]
    assert "chat" in ollama.calls
    assert "chat" not in claude.calls


async def test_claude_mode_routes_chat_to_claude(tmp_path):
    backend, ollama, claude = make_backend(tmp_path, mode="claude",
                                           fail_all_ollama=True)
    chunks = [c async for c in backend.chat([{"role": "user", "content": "hi"}])]
    assert chunks == ["Hi"]
    assert "chat" in claude.calls
    assert "chat" not in ollama.calls


async def test_claude_mode_never_loads_ollama(tmp_path):
    """The key test: in Claude mode, Ollama's chat and chat_with_tools must not be called."""
    backend, ollama, claude = make_backend(tmp_path, mode="claude",
                                           fail_all_ollama=True)
    # chat
    chunks = [c async for c in backend.chat([{"role": "user", "content": "hi"}])]
    # chat_with_tools
    events = [ev async for ev in backend.chat_with_tools(
        [{"role": "user", "content": "hi"}], [], lambda n, a: "r",
    )]
    # warm
    await backend.warm()
    assert "chat" not in ollama.calls
    assert "chat_with_tools" not in ollama.calls


async def test_embed_always_routes_to_ollama(tmp_path):
    """embed always goes to Ollama, even in Claude mode."""
    backend, ollama, claude = make_backend(tmp_path, mode="claude")
    result = await backend.embed(["hello"], "nomic-embed-text")
    assert "embed" in ollama.calls
    assert result == [[0.1, 0.1, 0.1]]


async def test_switching_to_claude_unloads_ollama(tmp_path):
    """set_mode("claude") doesn't unload — but the route handler does.
    This tests the backend unload() always targets Ollama."""
    backend, ollama, _ = make_backend(tmp_path, mode="local")
    backend.set_mode("claude")
    await backend.unload()
    assert ollama.unloaded


async def test_off_mode_model_paused_true(tmp_path):
    backend, _, _ = make_backend(tmp_path, mode="off")
    assert backend.model_paused() is True


async def test_local_mode_model_paused_false(tmp_path):
    backend, _, _ = make_backend(tmp_path, mode="local")
    assert backend.model_paused() is False


async def test_claude_mode_paused_when_usage_high(tmp_path):
    backend, _, claude = make_backend(tmp_path, mode="claude")
    claude.last_usage = {
        "five_hour": {"utilization": 0.85, "resets_at": 9999999999},
        "seven_day": {"utilization": 0.4, "resets_at": 9999999999},
    }
    assert backend.model_paused() is True


async def test_claude_mode_not_paused_when_usage_low(tmp_path):
    backend, _, claude = make_backend(tmp_path, mode="claude")
    claude.last_usage = {
        "five_hour": {"utilization": 0.3, "resets_at": 9999999999},
        "seven_day": {"utilization": 0.3, "resets_at": 9999999999},
    }
    assert backend.model_paused() is False


async def test_claude_mode_not_paused_when_no_usage(tmp_path):
    backend, _, claude = make_backend(tmp_path, mode="claude")
    claude.last_usage = None
    assert backend.model_paused() is False


# ── persistence ────────────────────────────────────────────────────────────────

def test_mode_persists_across_instances(tmp_path):
    conn1 = make_db(tmp_path)
    ollama1 = OllamaDouble()
    claude1 = ClaudeDouble()
    b1 = LLMBackend(ollama1, claude1, conn1)
    b1.set_mode("claude")
    b1.set_claude_model("sonnet")
    conn1.close()

    conn2 = make_db(tmp_path)
    ollama2 = OllamaDouble()
    claude2 = ClaudeDouble()
    b2 = LLMBackend(ollama2, claude2, conn2)
    assert b2.mode == "claude"
    assert b2.claude_model_key == "sonnet"
    conn2.close()


def test_fresh_install_defaults_to_local(tmp_path):
    conn = make_db(tmp_path)
    ollama = OllamaDouble()
    claude = ClaudeDouble()
    b = LLMBackend(ollama, claude, conn)
    assert b.mode == "local"


def test_migration_from_model_disabled(tmp_path):
    """An existing connection_state.model=0 row migrates to mode='off'."""
    conn = make_db(tmp_path)
    conn.execute(
        "INSERT OR REPLACE INTO connection_state (name, enabled) VALUES ('model', 0)")
    conn.commit()
    b = LLMBackend(OllamaDouble(), ClaudeDouble(), conn)
    assert b.mode == "off"


def test_set_mode_enabled_false_sets_off(tmp_path):
    conn = make_db(tmp_path)
    b = LLMBackend(OllamaDouble(), ClaudeDouble(), conn)
    b.set_mode("local")
    b.set_mode_enabled(False)
    assert b.mode == "off"


def test_set_mode_enabled_true_restores_local(tmp_path):
    conn = make_db(tmp_path)
    b = LLMBackend(OllamaDouble(), ClaudeDouble(), conn)
    b.set_mode("local")
    b.set_mode_enabled(False)
    b.set_mode_enabled(True)
    assert b.mode == "local"


def test_set_mode_enabled_true_restores_claude(tmp_path):
    conn = make_db(tmp_path)
    b = LLMBackend(OllamaDouble(), ClaudeDouble(), conn)
    b.set_mode("claude")
    b.set_mode_enabled(False)
    b.set_mode_enabled(True)
    assert b.mode == "claude"


# ── model_state dict shape ─────────────────────────────────────────────────────

def test_model_state_shape_local(tmp_path):
    conn = make_db(tmp_path)
    b = LLMBackend(OllamaDouble(), ClaudeDouble(), conn)
    b.set_mode("local")
    s = b.model_state()
    assert s["enabled"] is True
    assert s["mode"] == "local"
    assert s["runtime"] == "ollama"
    assert "local_name" in s
    assert "claude_model" in s
    assert "claude_models" in s
    assert "claude" in s


def test_model_state_shape_claude(tmp_path):
    conn = make_db(tmp_path)
    b = LLMBackend(OllamaDouble(), ClaudeDouble(), conn)
    b.set_mode("claude")
    s = b.model_state()
    assert s["enabled"] is True
    assert s["mode"] == "claude"
    assert s["runtime"] == "claude-cli"
    assert "Claude" in s["name"]


def test_model_state_shape_off(tmp_path):
    conn = make_db(tmp_path)
    b = LLMBackend(OllamaDouble(), ClaudeDouble(), conn)
    b.set_mode("off")
    s = b.model_state()
    assert s["enabled"] is False
    assert s["mode"] == "off"


def test_is_loaded_true_in_claude_mode(tmp_path):
    conn = make_db(tmp_path)
    b = LLMBackend(OllamaDouble(), ClaudeDouble(), conn)
    b.set_mode("claude")

    async def _check():
        return await b.is_loaded()

    import asyncio
    result = asyncio.run(_check())
    assert result is True


# ── startup + status (review fixes) ───────────────────────────────────────────

def test_persisted_claude_model_applied_on_startup(tmp_path):
    """A restart must not show Sonnet in Settings while Haiku answers."""
    backend, _, _ = make_backend(tmp_path, mode="claude")
    backend.set_claude_model("sonnet")
    conn = backend._conn
    fresh = ClaudeDouble(model="claude-haiku-4-5-20251001")
    LLMBackend(OllamaDouble(), fresh, conn)
    assert fresh.model == "claude-sonnet-5"


async def test_refresh_status_populates_model_state(tmp_path):
    backend, _, claude = make_backend(tmp_path, mode="claude")

    async def status():
        claude._auth_cache = (0.0, {"installed": True, "logged_in": True,
                                    "account": "me@example.com"})
        return claude._auth_cache[1]
    claude.status = status
    assert backend.model_state()["claude"]["installed"] is False
    await backend.refresh_status()
    st = backend.model_state()["claude"]
    assert st["installed"] is True and st["account"] == "me@example.com"
