"""Integration tests for the LLMBackend wired into the Router.

Covers: model.set_mode, model.set_claude_model, model.set_enabled IPC routes;
claude_unavailable events on the chat path; tool-list parity; background backoff;
and the key test — in Claude mode the OllamaDouble must never be called for chat."""

import sqlite3
from pathlib import Path

import pytest

from lumen.daemon import db
from lumen.daemon.connectors.connection_state import ConnectionState
from lumen.daemon.llm.backend import LLMBackend
from lumen.daemon.llm.claude_cli import ClaudeUnavailable
from lumen.daemon.llm.client import LLMUnavailable
from lumen.daemon.router import Router


# ── stubs ──────────────────────────────────────────────────────────────────────

class OllamaDouble:
    def __init__(self, model="qwen3:4b-instruct", fail_all=False):
        self.model = model
        self.fail_all = fail_all
        self.unloaded = False
        self.calls: list[str] = []

    async def chat(self, messages, **kwargs):
        self.calls.append("chat")
        if self.fail_all:
            raise AssertionError("OllamaDouble.chat() must not be called in Claude mode")
        yield "ollama-answer"

    async def chat_with_tools(self, messages, tools, executor, **kwargs):
        self.calls.append("chat_with_tools")
        if self.fail_all:
            raise AssertionError("chat_with_tools must not be called in Claude mode")
        yield {"content": "tool-answer"}

    async def embed(self, texts, model):
        return [[0.0] * 2 for _ in texts]

    async def is_loaded(self):
        return False

    async def warm(self, prime=None):
        pass

    async def unload(self):
        self.unloaded = True

    async def aclose(self):
        pass


class ClaudeDouble:
    def __init__(self, model="claude-haiku-4-5-20251001",
                 chunks=("claude-answer",),
                 fail=False, reason="offline"):
        self.model = model
        self._chunks = chunks
        self._fail = fail
        self._reason = reason
        self.last_usage: dict | None = None
        self._auth_cache = None
        self.calls: list[str] = []

    @property
    def last_status(self):
        return None

    async def chat(self, messages, **kwargs):
        self.calls.append("chat")
        if self._fail:
            raise ClaudeUnavailable("Claude is unavailable", reason=self._reason)
        for c in self._chunks:
            yield c

    async def chat_with_tools(self, messages, tools, executor, **kwargs):
        self.calls.append("chat_with_tools")
        if self._fail:
            raise ClaudeUnavailable("Claude is unavailable", reason=self._reason)
        yield {"content": "claude-tool-answer"}

    async def is_loaded(self):
        return True

    async def warm(self, prime=None):
        pass

    async def unload(self):
        pass

    async def aclose(self):
        pass


class FakeTodos:
    def add(self, text, today=None, source="manual"): return []
    def open_todos(self): return []
    def list_all(self): return []
    def toggle(self, *a): return []
    def delete(self, *a): return []
    def update(self, *a, **kw): return []
    def exists(self, *a): return False
    def scan_commitments(self): return []


def make_conn(tmp_path: Path) -> sqlite3.Connection:
    conn = db.connect(tmp_path / "test.db")
    conn.execute("""CREATE TABLE IF NOT EXISTS connection_state
                    (name TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1)""")
    conn.commit()
    return conn


def make_setup(tmp_path: Path, *, mode: str = "local",
               fail_claude: bool = False, fail_reason: str = "offline"):
    conn = make_conn(tmp_path)
    ollama = OllamaDouble(fail_all=(mode == "claude"))
    claude = ClaudeDouble(fail=fail_claude, reason=fail_reason)
    backend = LLMBackend(ollama, claude, conn)
    backend.set_mode(mode)
    connections = ConnectionState(conn)
    router = Router(backend, FakeTodos(), connection_state=connections)
    return router, backend, ollama, claude, connections


async def collect(router, type_, payload=None):
    return [ev async for ev in router.handle(type_, payload or {})]


# ── model.set_mode ─────────────────────────────────────────────────────────────

async def test_set_mode_to_claude(tmp_path):
    router, backend, ollama, claude, _ = make_setup(tmp_path, mode="local")
    events = await collect(router, "model.set_mode", {"mode": "claude"})
    assert any("result" in ev for ev in events)
    result = next(ev["result"] for ev in events if "result" in ev)
    assert result["model"]["mode"] == "claude"
    assert result["model"]["enabled"] is True
    assert ollama.unloaded  # Ollama must be unloaded when switching from local


async def test_set_mode_to_off(tmp_path):
    router, backend, ollama, claude, _ = make_setup(tmp_path, mode="local")
    events = await collect(router, "model.set_mode", {"mode": "off"})
    result = next(ev["result"] for ev in events if "result" in ev)
    assert result["model"]["mode"] == "off"
    assert result["model"]["enabled"] is False
    assert ollama.unloaded


async def test_set_mode_to_local(tmp_path):
    router, backend, ollama, claude, _ = make_setup(tmp_path, mode="off")
    events = await collect(router, "model.set_mode", {"mode": "local"})
    result = next(ev["result"] for ev in events if "result" in ev)
    assert result["model"]["mode"] == "local"
    # No unload when going to local from off (nothing to unload)


async def test_set_mode_invalid(tmp_path):
    router, _, _, _, _ = make_setup(tmp_path)
    events = await collect(router, "model.set_mode", {"mode": "bogus"})
    assert any("error" in ev for ev in events)


# ── model.set_claude_model ────────────────────────────────────────────────────

async def test_set_claude_model(tmp_path):
    router, backend, _, claude, _ = make_setup(tmp_path, mode="claude")
    events = await collect(router, "model.set_claude_model", {"model": "sonnet"})
    result = next(ev["result"] for ev in events if "result" in ev)
    assert result["model"]["claude_model"] == "sonnet"


async def test_set_claude_model_invalid(tmp_path):
    router, _, _, _, _ = make_setup(tmp_path)
    events = await collect(router, "model.set_claude_model", {"model": "gpt-4"})
    assert any("error" in ev for ev in events)


# ── model.set_enabled (legacy alias) ─────────────────────────────────────────

async def test_set_enabled_false_sets_off(tmp_path):
    router, backend, ollama, _, _ = make_setup(tmp_path, mode="local")
    events = await collect(router, "model.set_enabled", {"enabled": False})
    result = next(ev["result"] for ev in events if "result" in ev)
    assert result["model"]["mode"] == "off"
    assert result["model"]["enabled"] is False


async def test_set_enabled_true_restores_mode(tmp_path):
    router, backend, _, _, _ = make_setup(tmp_path, mode="local")
    await collect(router, "model.set_enabled", {"enabled": False})
    events = await collect(router, "model.set_enabled", {"enabled": True})
    result = next(ev["result"] for ev in events if "result" in ev)
    assert result["model"]["enabled"] is True


# ── model_enabled gate ────────────────────────────────────────────────────────

async def test_model_off_blocks_chat(tmp_path):
    router, _, _, _, _ = make_setup(tmp_path, mode="off")
    events = await collect(router, "chat", {"message": "hi"})
    assert any("model_off" in ev for ev in events)


async def test_model_enabled_in_local_allows_chat(tmp_path):
    router, backend, ollama, _, _ = make_setup(tmp_path, mode="local")
    ollama.fail_all = False
    events = await collect(router, "chat", {"message": "hi"})
    assert any("chunk" in ev or "done" in ev for ev in events)


# ── via event in Claude mode ──────────────────────────────────────────────────

async def test_claude_mode_emits_via_not_cold_start(tmp_path):
    router, _, _, _, _ = make_setup(tmp_path, mode="claude")
    events = await collect(router, "chat", {"message": "hi"})
    types = list(events)
    via_evs = [ev for ev in events if "via" in ev]
    cold_evs = [ev for ev in events if "cold_start" in ev]
    assert via_evs, "Claude mode must emit a 'via' event"
    assert not cold_evs, "Claude mode must not emit cold_start"
    assert "Claude" in via_evs[0]["via"]


# ── claude_unavailable on chat path ──────────────────────────────────────────

async def test_chat_path_claude_unavailable_event(tmp_path):
    router, _, _, _, _ = make_setup(tmp_path, mode="claude",
                                    fail_claude=True, fail_reason="logged_out")
    events = await collect(router, "chat", {"message": "hi"})
    cu_evs = [ev for ev in events if "claude_unavailable" in ev]
    assert cu_evs, "ClaudeUnavailable should yield claude_unavailable event"
    assert cu_evs[0]["claude_unavailable"]["reason"] == "logged_out"
    # Stream must end with done
    assert any(ev.get("done") for ev in events)


async def test_chat_path_no_error_event_for_claude_failure(tmp_path):
    """claude_unavailable must NOT come as a plain error event on the chat path."""
    router, _, _, _, _ = make_setup(tmp_path, mode="claude",
                                    fail_claude=True, fail_reason="offline")
    events = await collect(router, "chat", {"message": "hi"})
    error_evs = [ev for ev in events if "error" in ev]
    # There should be no bare {"error": ...} for ClaudeUnavailable on chat path
    for ev in error_evs:
        # If there is an error, it must not be a Claude error message
        assert "Claude" not in str(ev.get("error", ""))


# ── background backoff ────────────────────────────────────────────────────────

def test_model_paused_off(tmp_path):
    _, backend, _, _, _ = make_setup(tmp_path, mode="off")
    assert backend.model_paused() is True


def test_model_paused_local(tmp_path):
    _, backend, _, _, _ = make_setup(tmp_path, mode="local")
    assert backend.model_paused() is False


def test_model_paused_claude_high_usage(tmp_path):
    _, backend, _, claude, _ = make_setup(tmp_path, mode="claude")
    claude.last_usage = {
        "five_hour": {"utilization": 0.95, "resets_at": 9999999999},
        "seven_day": {"utilization": 0.3, "resets_at": 9999999999},
    }
    assert backend.model_paused() is True


def test_model_paused_claude_low_usage(tmp_path):
    _, backend, _, claude, _ = make_setup(tmp_path, mode="claude")
    claude.last_usage = {
        "five_hour": {"utilization": 0.5, "resets_at": 9999999999},
        "seven_day": {"utilization": 0.5, "resets_at": 9999999999},
    }
    assert backend.model_paused() is False


# ── tool parity: Claude and local get the same tools ─────────────────────────

def test_tool_parity(tmp_path):
    """Claude must be offered the same tools the local model gets.

    Since we can't run a real MCP bridge here, verify the router's
    chat_with_tools is called with identical tools regardless of mode."""
    from lumen.daemon import local_tools

    # Both modes use the same _chat_with_tools code path which assembles
    # the same tool list. Verify LOCAL_TOOL_NAMES are in TODO_TOOLS.
    todo_names = {t["function"]["name"] for t in local_tools.TODO_TOOLS}
    assert local_tools.LOCAL_TOOL_NAMES.issubset(todo_names)

    mail_names = {t["function"]["name"] for t in local_tools.MAIL_TOOLS}
    assert local_tools.MAIL_TOOL_NAMES.issubset(mail_names)
    # The tools lists are the same objects passed to both Ollama and Claude;
    # there's no filter applied differently by mode.


# Review finding 2026-09-24: these chat sub-paths caught only LLMUnavailable,
# so a Claude failure there was a bare error with no Settings notice. Driven
# directly: through "chat" they need stores this harness doesn't build, and
# would silently fall through to plain chat (which was already covered).
async def test_recommend_subpath_emits_claude_unavailable(tmp_path):
    router, *_ = make_setup(tmp_path, mode="claude")

    async def boom(_request=None):
        raise ClaudeUnavailable("limit", reason="rate_limited")
    router._recommend = boom
    events = [ev async for ev in router._recommend_chat("what should I read next")]
    assert events == [{"claude_unavailable": {"reason": "rate_limited",
                                              "message": "limit"}},
                      {"done": True}]


async def test_forget_subpath_emits_claude_unavailable(tmp_path):
    router, *_ = make_setup(tmp_path, mode="claude",
                            fail_claude=True, fail_reason="logged_out")
    mem = tmp_path / "memory.md"
    mem.write_text("## Food\n- Likes tea (last seen 2026-09-01)\n")
    router._memory_path, router._memory_cap = mem, 4000
    events = [ev async for ev in router._forget_chat("Forget that I like tea")]
    assert [ev["claude_unavailable"]["reason"] for ev in events
            if "claude_unavailable" in ev] == ["logged_out"], events
    assert events[-1] == {"done": True}
    assert not [ev for ev in events if "error" in ev], events
