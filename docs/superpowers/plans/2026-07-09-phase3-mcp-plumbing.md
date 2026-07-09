# Phase 3 — MCP Plumbing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove an MCP tool-calling loop end-to-end from the daemon: the local model calls a read-only MCP tool and returns an answer visibly grounded in the tool output, verifiable against a JSONL tool-call log — demonstrated against both an off-the-shelf filesystem server and a thin custom Open Library server.

**Architecture:** Approach A — the MCP client and the agentic tool loop live inside `daemon/llm/`, keeping Ollama's native `tools` API (no llama.cpp swap). A cheap `TOOL_HINT` heuristic in `router.py` gates whether tools are attached; when they are, `OllamaClient.chat_with_tools` runs non-streamed turns (Ollama returns `tool_calls` reliably only non-streamed), executing each call through `MCPBridge` and feeding results back until the model answers. A per-server read-only tool allowlist keeps Phase 3 structurally read-only.

**Tech Stack:** Python 3.12, `mcp` (Model Context Protocol SDK), Ollama HTTP API via httpx, PyQt6, pytest / pytest-asyncio / pytest-qt, `uv` for everything (`uv run pytest`, `uv add`).

**Spec:** `docs/superpowers/specs/2026-07-09-phase3-mcp-plumbing-design.md` (approved 2026-07-09).

## Global Constraints

- Run tests with `uv run pytest` (asyncio_mode=auto; Qt tests use the offscreen platform via `tests/conftest.py`).
- New dependency this phase: `mcp` (added in Task 1). No others.
- All LLM calls stay in `daemon/llm/`. The router adds context / selects tools; it never calls Ollama directly.
- Idle-unload behavior untouched: `chat_with_tools` sends `keep_alive` on every request exactly like `chat`; MCP server subprocesses carry no model and do not affect idle-unload.
- Read-only only: no write tools are exposed this phase. The filesystem server is restricted to a read allowlist in config. No write-confirmation flow yet (Phase 5).
- UI holds zero business logic: the daemon decides whether a tool ran and emits a `{"tool_used": name}` event; the launcher only renders it.
- Terse over clever. Match existing file style (module docstrings, minimal comments, fake-collaborator tests like `FakeLLM`/`FakeStore`).
- Commit messages: imperative subject, NO Co-Authored-By or any credit trailer, and the final line must be `This commit used N prompts.` where N = user prompts since the last push, counted at commit time (N=3 as of plan writing — the "start phase 3", "approach A + autonomy", and "implement with subagents" prompts; recompute if more prompts have arrived before your commit).

## File map

| File | Action | Responsibility |
|---|---|---|
| `pyproject.toml` | modify | add `mcp` dependency |
| `lumen/daemon/config.py` | modify | `[mcp]` parsing → `MCPConfig` + `MCPServerConfig` |
| `lumen/config.example.toml` | modify | document `[mcp]` + `[[mcp.servers]]` |
| `lumen/daemon/llm/mcp_bridge.py` | replace stub | schema conversion, result flatten, `MCPBridge`, stdio `connect_servers` |
| `lumen/daemon/llm/client.py` | modify | add `chat_with_tools` (non-streamed agentic loop) |
| `lumen/daemon/llm/model_router.py` | replace stub | `ModelRouter.pick_model` (fast-model seam) |
| `lumen/daemon/llm/tool_log.py` | create | JSONL tool-call log writer |
| `lumen/daemon/router.py` | modify | `TOOL_HINT` gate, tool-loop path, `tool_used` event |
| `lumen/daemon/__main__.py` | modify | wire bridge + model_router + tool_log into Router |
| `lumen/ui/daemon_client.py` | modify | `tool_used` signal + IPC branch |
| `lumen/ui/launcher.py` | modify | render `🔧 used <tool>` marker |
| `lumen/mcp_servers/__init__.py` | create | package marker |
| `lumen/mcp_servers/openlibrary.py` | create | custom read-only Open Library MCP server |
| `.claude/skills/mcp-integration.md`, `.claude/skills/llm-serving.md`, `.claude/skills/development-plan.md` | modify | record decisions; mark Phase 3 done |
| tests: `test_config.py`, `llm/test_mcp_bridge.py` (new), `llm/test_client.py`, `llm/test_tool_log.py` (new), `test_router.py`, `ui/test_daemon_client.py`, `ui/test_launcher.py`, `mcp_servers/test_openlibrary.py` (new) | | |

## Dependency graph (for parallel dispatch)

- **Wave 1 (independent, disjoint files):** Task 1 (config+dep), Task 2 (bridge logic), Task 4 (chat_with_tools), Task 8 (UI event).
- **Wave 2:** Task 3 (stdio connect — needs Task 2's shape + Task 1's dep), Task 9 (Open Library server — needs Task 1's dep), Task 5 (model_router — trivial, independent).
- **Wave 3:** Task 6 (router — needs 1,2,4,5), then Task 7 (tool log wiring — same file as 6, so sequential after 6).
- **Wave 4:** Task 10 (verification + docs — needs everything).

Files that collide must not run in parallel: Tasks 2 & 3 (both `mcp_bridge.py`); Tasks 6 & 7 (both `router.py`); Tasks 1 & 9 (both `config.example.toml` — Task 9 only appends its server block, do Task 1 first).

---

### Task 1: `[mcp]` config + add the `mcp` dependency

**Files:**
- Modify: `pyproject.toml`
- Modify: `lumen/daemon/config.py`
- Modify: `lumen/config.example.toml`
- Test: `tests/daemon/test_config.py`

**Interfaces:**
- Produces: `MCPServerConfig(name: str, command: str, args: list[str], tools: tuple[str, ...] | None)`; `MCPConfig(enabled: bool, max_iterations: int, log_path: Path, servers: tuple[MCPServerConfig, ...])`; `Config.mcp: MCPConfig` (default: disabled, no servers); `default_tool_log_path() -> Path`.

- [ ] **Step 1: Add the dependency**

Run: `uv add mcp`
Expected: `pyproject.toml` gains `mcp>=...` under `[project].dependencies`; `uv.lock` updates; import works: `uv run python -c "import mcp; from mcp.server.fastmcp import FastMCP; print('ok')"` prints `ok`.

- [ ] **Step 2: Write the failing tests** — append to `tests/daemon/test_config.py`:

```python
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
                        ["-y", "@modelcontextprotocol/server-filesystem", "~/notes"],
                        ("read_file", "list_directory")),
    )


def test_mcp_server_without_tools_has_none_allowlist(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[mcp]\nenabled = true\n[[mcp.servers]]\nname = "books"\ncommand = "python"\nargs = ["-m", "x"]\n')
    cfg = load_config(p)
    assert cfg.mcp.servers[0].tools is None


def test_default_tool_log_path_honors_xdg_state(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert default_tool_log_path() == tmp_path / "lumen" / "tool-calls.jsonl"
```

- [ ] **Step 3: Run to verify they fail**

Run: `uv run pytest tests/daemon/test_config.py -k mcp -v`
Expected: FAIL (ImportError: cannot import name `MCPConfig`).

- [ ] **Step 4: Implement** — in `lumen/daemon/config.py`, add after `default_db_path`:

```python
def default_tool_log_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "lumen" / "tool-calls.jsonl"


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    command: str
    args: list[str]
    tools: tuple[str, ...] | None = None   # read-only allowlist; None = expose all


@dataclass(frozen=True)
class MCPConfig:
    enabled: bool = False
    max_iterations: int = 4
    log_path: Path = field(default_factory=default_tool_log_path)
    servers: tuple[MCPServerConfig, ...] = ()
```

Add the field to `Config`:

```python
    mcp: "MCPConfig" = field(default_factory=lambda: MCPConfig())
```

In `load_config`, after the storage block and before the `idle_unload_minutes` guard, add:

```python
    mcp_raw = data.get("mcp")
    if mcp_raw is not None:
        servers = tuple(
            MCPServerConfig(
                name=s["name"],
                command=s["command"],
                args=list(s.get("args", [])),
                tools=tuple(s["tools"]) if "tools" in s else None,
            )
            for s in mcp_raw.get("servers", [])
        )
        mcp_kwargs = {"enabled": bool(mcp_raw.get("enabled", False)), "servers": servers}
        if "max_iterations" in mcp_raw:
            mcp_kwargs["max_iterations"] = int(mcp_raw["max_iterations"])
        if mcp_raw.get("log_path"):
            mcp_kwargs["log_path"] = Path(mcp_raw["log_path"]).expanduser()
        kwargs["mcp"] = MCPConfig(**mcp_kwargs)
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/daemon/test_config.py -v`
Expected: PASS (all config tests, old and new).

- [ ] **Step 6: Document the block** — append to `lumen/config.example.toml`:

```toml

[mcp]
# enabled = false            # attach MCP tools to chat when a message looks like a lookup
# max_iterations = 4         # tool-loop safety cap per request
# log_path = ""              # default: $XDG_STATE_HOME/lumen/tool-calls.jsonl (grounding-verification log)

# One [[mcp.servers]] block per server. `tools` is a read-only allowlist:
# if present, ONLY these tool names are exposed to the model (keeps Phase 3 read-only).
# [[mcp.servers]]
# name = "fs"
# command = "npx"
# args = ["-y", "@modelcontextprotocol/server-filesystem", "~/notes"]
# tools = ["read_file", "read_multiple_files", "list_directory", "directory_tree", "search_files", "get_file_info"]

# [[mcp.servers]]
# name = "books"
# command = "python"
# args = ["-m", "lumen.mcp_servers.openlibrary"]
# tools = ["search_books", "get_book"]
```

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock lumen/daemon/config.py lumen/config.example.toml tests/daemon/test_config.py
git commit -m "Add [mcp] config parsing and the mcp dependency

This commit used N prompts."
```

---

### Task 2: MCP bridge — schema conversion, result flatten, aggregation

**Files:**
- Replace stub: `lumen/daemon/llm/mcp_bridge.py`
- Test: `tests/daemon/llm/test_mcp_bridge.py` (new)

**Interfaces:**
- Produces:
  - `class ToolCallError(Exception)`
  - `tool_to_ollama_schema(name: str, description: str, input_schema: dict) -> dict` — returns `{"type":"function","function":{"name","description","parameters"}}`.
  - `flatten_content(blocks) -> str` — joins `.text` of text blocks; duck-typed on `.type`/`.text`; notes non-text blocks as `[<type>]`.
  - `class MCPBridge` constructed as `MCPBridge(clients: dict[str, ClientLike], allowlists: dict[str, tuple[str,...] | None])`, where each client has `async list_tools() -> list[ToolLike]` (`.name`,`.description`,`.inputSchema`) and `async call_tool(name, args) -> ResultLike` (`.content`, `.isError`). Methods: `async load_tools() -> None`; `ollama_tools() -> list[dict]`; `async call(name: str, arguments: dict) -> str` (raises `ToolCallError` on unknown name or `isError`). Namespacing: bare tool name when unique; `"<server>__<tool>"` on collision.
- Consumes: nothing from other tasks.

- [ ] **Step 1: Write the failing tests** — create `tests/daemon/llm/test_mcp_bridge.py`:

```python
import pytest

from lumen.daemon.llm.mcp_bridge import (
    MCPBridge, ToolCallError, flatten_content, tool_to_ollama_schema,
)


class T:  # tool-like
    def __init__(self, name, description="", input_schema=None):
        self.name = name
        self.description = description
        self.inputSchema = input_schema or {"type": "object", "properties": {}}


class Block:
    def __init__(self, type, text=""):
        self.type = type
        self.text = text


class R:  # call result-like
    def __init__(self, content, is_error=False):
        self.content = content
        self.isError = is_error


class FakeClient:
    def __init__(self, tools, results=None):
        self._tools = tools
        self._results = results or {}
        self.calls = []

    async def list_tools(self):
        return self._tools

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        return self._results.get(name, R([Block("text", "default")]))


def test_tool_to_ollama_schema_shape():
    s = tool_to_ollama_schema("read_file", "reads a file", {"type": "object", "properties": {"path": {"type": "string"}}})
    assert s["type"] == "function"
    assert s["function"]["name"] == "read_file"
    assert s["function"]["description"] == "reads a file"
    assert s["function"]["parameters"]["properties"]["path"]["type"] == "string"


def test_flatten_content_joins_text_and_marks_nontext():
    assert flatten_content([Block("text", "a"), Block("text", "b")]) == "a\nb"
    assert "[image]" in flatten_content([Block("image")])


async def test_ollama_tools_filtered_by_allowlist():
    client = FakeClient([T("read_file"), T("write_file"), T("list_directory")])
    bridge = MCPBridge({"fs": client}, {"fs": ("read_file", "list_directory")})
    await bridge.load_tools()
    names = {t["function"]["name"] for t in bridge.ollama_tools()}
    assert names == {"read_file", "list_directory"}       # write_file structurally excluded


async def test_call_routes_and_flattens():
    client = FakeClient([T("read_file")], results={"read_file": R([Block("text", "file contents")])})
    bridge = MCPBridge({"fs": client}, {"fs": None})
    await bridge.load_tools()
    assert await bridge.call("read_file", {"path": "/x"}) == "file contents"
    assert client.calls == [("read_file", {"path": "/x"})]


async def test_call_unknown_tool_raises():
    bridge = MCPBridge({"fs": FakeClient([T("read_file")])}, {"fs": None})
    await bridge.load_tools()
    with pytest.raises(ToolCallError):
        await bridge.call("nonexistent", {})


async def test_call_tool_error_raises():
    client = FakeClient([T("read_file")], results={"read_file": R([Block("text", "nope")], is_error=True)})
    bridge = MCPBridge({"fs": client}, {"fs": None})
    await bridge.load_tools()
    with pytest.raises(ToolCallError):
        await bridge.call("read_file", {"path": "/x"})


async def test_collision_namespaced_by_server():
    a = FakeClient([T("search")])
    b = FakeClient([T("search")])
    bridge = MCPBridge({"fs": a, "books": b}, {"fs": None, "books": None})
    await bridge.load_tools()
    names = {t["function"]["name"] for t in bridge.ollama_tools()}
    assert names == {"fs__search", "books__search"}
    await bridge.call("books__search", {"q": "x"})
    assert b.calls == [("search", {"q": "x"})]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/daemon/llm/test_mcp_bridge.py -v`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implement** — replace `lumen/daemon/llm/mcp_bridge.py` with:

```python
"""MCP → Ollama bridge. Converts MCP tool schemas to Ollama's `tools` format,
routes tool calls to the owning server, and flattens results to text. The stdio
subprocess wiring lives in connect_servers (Task 3); MCPBridge itself is pure
aggregation over injected clients so it unit-tests without a subprocess."""

from collections import Counter


class ToolCallError(Exception):
    """A tool call could not be completed (unknown tool, or the server flagged an error)."""


def tool_to_ollama_schema(name: str, description: str, input_schema: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description or "",
            "parameters": input_schema or {"type": "object", "properties": {}},
        },
    }


def flatten_content(blocks) -> str:
    parts = []
    for b in blocks or []:
        if getattr(b, "type", None) == "text":
            parts.append(getattr(b, "text", "") or "")
        else:
            parts.append(f"[{getattr(b, 'type', 'unknown')}]")
    return "\n".join(parts)


class MCPBridge:
    def __init__(self, clients: dict, allowlists: dict):
        self._clients = clients                 # server_name -> client
        self._allow = allowlists                # server_name -> tuple[str,...] | None
        self._schemas: list[dict] = []          # Ollama tool schemas (namespaced names)
        self._registry: dict[str, tuple] = {}   # exposed_name -> (server_name, original_name)

    async def load_tools(self) -> None:
        """List tools from every client, apply allowlists, and resolve name collisions."""
        raw = {}  # server_name -> list[(original_name, description, input_schema)]
        for server, client in self._clients.items():
            allow = self._allow.get(server)
            tools = []
            for t in await client.list_tools():
                if allow is not None and t.name not in allow:
                    continue
                tools.append((t.name, t.description, t.inputSchema))
            raw[server] = tools
        counts = Counter(name for tools in raw.values() for name, _, _ in tools)
        self._schemas, self._registry = [], {}
        for server, tools in raw.items():
            for name, desc, schema in tools:
                exposed = name if counts[name] == 1 else f"{server}__{name}"
                self._registry[exposed] = (server, name)
                self._schemas.append(tool_to_ollama_schema(exposed, desc, schema))

    def ollama_tools(self) -> list[dict]:
        return list(self._schemas)

    async def call(self, name: str, arguments: dict) -> str:
        target = self._registry.get(name)
        if target is None:
            raise ToolCallError(f"unknown tool: {name}")
        server, original = target
        result = await self._clients[server].call_tool(original, arguments)
        if getattr(result, "isError", False):
            raise ToolCallError(f"{name} failed: {flatten_content(result.content)[:200]}")
        return flatten_content(result.content)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/daemon/llm/test_mcp_bridge.py -v`
Expected: PASS (all 7 tests).

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/llm/mcp_bridge.py tests/daemon/llm/test_mcp_bridge.py
git commit -m "Add MCPBridge: MCP→Ollama schema conversion, allowlist, routing

This commit used N prompts."
```

---

### Task 3: stdio server connection — `MCPClient` + `connect_servers`

**Files:**
- Modify: `lumen/daemon/llm/mcp_bridge.py`
- Test: `tests/daemon/llm/test_mcp_bridge.py` (append)

**Interfaces:**
- Consumes: `MCPBridge`, `flatten_content` (Task 2); `MCPServerConfig` (Task 1).
- Produces: `class MCPClient` wrapping one connected `ClientSession` (`async list_tools()`, `async call_tool(name, args)`); `async connect_servers(servers: list[MCPServerConfig]) -> MCPBridge` — spawns each server's stdio subprocess (guarded: a server that fails to launch is logged and skipped), builds an `MCPBridge`, calls `load_tools()`, returns it; the bridge grows an `async aclose()` that tears the subprocesses down. Lazy start is the caller's job (Task 6 calls `connect_servers` on first tool need).

- [ ] **Step 1: Write the test** (config-translation only; the live stdio path is smoke-tested manually in Task 10 because it needs `npx`/node) — append to `tests/daemon/llm/test_mcp_bridge.py`:

```python
from lumen.daemon.config import MCPServerConfig
from lumen.daemon.llm.mcp_bridge import server_params


def test_server_params_builds_stdio_command():
    cfg = MCPServerConfig("fs", "npx", ["-y", "pkg", "~/notes"], ("read_file",))
    p = server_params(cfg)
    assert p.command == "npx"
    assert p.args == ["-y", "pkg", "~/notes"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/daemon/llm/test_mcp_bridge.py::test_server_params_builds_stdio_command -v`
Expected: FAIL (ImportError: `server_params`).

- [ ] **Step 3: Implement** — append to `lumen/daemon/llm/mcp_bridge.py`:

```python
import logging
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from lumen.daemon.config import MCPServerConfig

log = logging.getLogger(__name__)


def server_params(cfg: MCPServerConfig) -> StdioServerParameters:
    return StdioServerParameters(command=cfg.command, args=cfg.args)


class MCPClient:
    """One connected MCP server session. Normalizes results to the shapes MCPBridge expects."""

    def __init__(self, session: ClientSession):
        self._session = session

    async def list_tools(self):
        return (await self._session.list_tools()).tools

    async def call_tool(self, name: str, arguments: dict):
        return await self._session.call_tool(name, arguments)


async def connect_servers(servers: list[MCPServerConfig]) -> MCPBridge:
    """Spawn each server's stdio subprocess and return a loaded MCPBridge.
    A server that fails to launch is logged and skipped, never crashing the daemon."""
    stack = AsyncExitStack()
    clients, allowlists = {}, {}
    for cfg in servers:
        try:
            read, write = await stack.enter_async_context(stdio_client(server_params(cfg)))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            clients[cfg.name] = MCPClient(session)
            allowlists[cfg.name] = cfg.tools
        except Exception:
            log.exception("MCP server %r failed to start — skipping", cfg.name)
    bridge = MCPBridge(clients, allowlists)
    bridge._stack = stack  # own the subprocesses' lifetime
    await bridge.load_tools()
    return bridge
```

Add to `MCPBridge.__init__`: `self._stack = None`. Add method to `MCPBridge`:

```python
    async def aclose(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/daemon/llm/test_mcp_bridge.py -v`
Expected: PASS (Task 2 tests + the new one).

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/llm/mcp_bridge.py tests/daemon/llm/test_mcp_bridge.py
git commit -m "Add stdio MCPClient and connect_servers factory

This commit used N prompts."
```

---

### Task 4: Tool-aware chat loop — `OllamaClient.chat_with_tools`

**Files:**
- Modify: `lumen/daemon/llm/client.py`
- Test: `tests/daemon/llm/test_client.py` (append)

**Interfaces:**
- Produces: `async def chat_with_tools(self, messages: list[dict], tools: list[dict], executor, *, model: str | None = None, max_iterations: int = 4) -> AsyncIterator[dict]`. `executor` is `async (name: str, arguments: dict) -> str` returning the tool result text (executor owns error handling/logging — Task 6). Yields, in order: `{"tool_call": {"name", "arguments"}}` per invoked tool, then finally `{"content": str}`; if the iteration cap is hit, yields `{"content": "", "capped": True}`. Raises `LLMUnavailable` on transport/404 errors like `chat`.
- Consumes: nothing from other tasks.

- [ ] **Step 1: Write the failing tests** — append to `tests/daemon/llm/test_client.py`:

```python
async def test_chat_with_tools_executes_then_answers():
    turns = iter([
        # turn 1: model asks for a tool
        httpx.Response(200, json={"message": {"role": "assistant", "content": "",
            "tool_calls": [{"function": {"name": "list_directory", "arguments": {"path": "/n"}}}]},
            "done": True}),
        # turn 2: model answers using the tool result
        httpx.Response(200, json={"message": {"role": "assistant", "content": "You have a.txt and b.txt."},
            "done": True}),
    ])
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return next(turns)

    executed = []

    async def executor(name, args):
        executed.append((name, args))
        return "a.txt\nb.txt"

    client = make_client(handler)
    events = [e async for e in client.chat_with_tools(
        [{"role": "user", "content": "what files are in /n"}],
        tools=[{"type": "function", "function": {"name": "list_directory"}}],
        executor=executor)]
    assert executed == [("list_directory", {"path": "/n"})]
    assert {"tool_call": {"name": "list_directory", "arguments": {"path": "/n"}}} in events
    assert events[-1] == {"content": "You have a.txt and b.txt."}
    # tools attached and keep_alive still enforced on the tool turn
    assert sent[0]["tools"][0]["function"]["name"] == "list_directory"
    assert sent[0]["keep_alive"] == "10m"
    assert sent[0]["stream"] is False
    # tool result fed back as a role:tool message
    assert any(m.get("role") == "tool" and m.get("content") == "a.txt\nb.txt"
               for m in sent[1]["messages"])
    await client.aclose()


async def test_chat_with_tools_direct_answer_no_tool():
    def handler(request):
        return httpx.Response(200, json={"message": {"content": "42"}, "done": True})

    async def executor(name, args):
        raise AssertionError("should not be called")

    client = make_client(handler)
    events = [e async for e in client.chat_with_tools(
        [{"role": "user", "content": "2+2*20"}], tools=[], executor=executor)]
    assert events == [{"content": "42"}]
    await client.aclose()


async def test_chat_with_tools_respects_iteration_cap():
    def handler(request):  # always asks for another tool → would loop forever
        return httpx.Response(200, json={"message": {"content": "",
            "tool_calls": [{"function": {"name": "t", "arguments": {}}}]}, "done": True})

    async def executor(name, args):
        return "again"

    client = make_client(handler)
    events = [e async for e in client.chat_with_tools(
        [{"role": "user", "content": "x"}], tools=[{"type": "function", "function": {"name": "t"}}],
        executor=executor, max_iterations=2)]
    assert events[-1] == {"content": "", "capped": True}
    assert sum(1 for e in events if "tool_call" in e) == 2
    await client.aclose()


async def test_chat_with_tools_unreachable_raises():
    def handler(request):
        raise httpx.ConnectError("refused")

    async def executor(name, args):
        return ""

    client = make_client(handler)
    with pytest.raises(LLMUnavailable):
        async for _ in client.chat_with_tools([{"role": "user", "content": "hi"}], [], executor):
            pass
    await client.aclose()
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/daemon/llm/test_client.py -k with_tools -v`
Expected: FAIL (AttributeError: no `chat_with_tools`).

- [ ] **Step 3: Implement** — add to `OllamaClient` in `lumen/daemon/llm/client.py`:

```python
    async def _post_chat(self, messages: list[dict], tools: list[dict], model: str | None) -> dict:
        body = {
            "model": model or self.model,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "keep_alive": self.keep_alive,
            "think": self.think,
        }
        try:
            resp = await self._http.post("/api/chat", json=body)
            if resp.status_code == 404:
                raise LLMUnavailable(
                    f"model '{model or self.model}' not found — run: ollama pull {model or self.model}"
                )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            raise LLMUnavailable(
                f"Ollama request failed ({type(e).__name__}) at {self.base_url} — is the "
                "service running? (systemctl --user status ollama)"
            ) from e

    async def chat_with_tools(self, messages, tools, executor, *, model=None, max_iterations=4):
        """Agentic loop: non-streamed turns detect tool_calls, execute them via
        `executor`, feed results back, and repeat until the model answers (or the cap)."""
        convo = list(messages)
        for _ in range(max_iterations):
            data = await self._post_chat(convo, tools, model)
            msg = data.get("message", {})
            calls = msg.get("tool_calls") or []
            if not calls:
                yield {"content": msg.get("content", "")}
                return
            convo.append(msg)
            for call in calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                args = fn.get("arguments") or {}
                yield {"tool_call": {"name": name, "arguments": args}}
                result_text = await executor(name, args)
                convo.append({"role": "tool", "content": result_text, "tool_name": name})
        yield {"content": "", "capped": True}
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/daemon/llm/test_client.py -v`
Expected: PASS (existing chat tests + 4 new).

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/llm/client.py tests/daemon/llm/test_client.py
git commit -m "Add chat_with_tools: non-streamed agentic tool loop

This commit used N prompts."
```

---

### Task 5: model_router seam — `ModelRouter.pick_model`

**Files:**
- Replace stub: `lumen/daemon/llm/model_router.py`
- Test: `tests/daemon/llm/test_model_router.py` (new)

**Interfaces:**
- Produces: `class ModelRouter` constructed `ModelRouter(fast_model: str, escalation_model: str | None = None)`; `pick_model(self, message: str, *, needs_tools: bool) -> str` — Phase 3 always returns `fast_model`.
- Consumes: nothing.

- [ ] **Step 1: Write the failing test** — create `tests/daemon/llm/test_model_router.py`:

```python
from lumen.daemon.llm.model_router import ModelRouter


def test_pick_model_returns_fast_model_for_now():
    mr = ModelRouter("qwen3:4b-instruct", escalation_model="qwen3:14b")
    assert mr.pick_model("who wrote Dune?", needs_tools=True) == "qwen3:4b-instruct"
    assert mr.pick_model("2+2", needs_tools=False) == "qwen3:4b-instruct"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/daemon/llm/test_model_router.py -v`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implement** — replace `lumen/daemon/llm/model_router.py`:

```python
"""Picks which model handles a request. Phase 3: always the fast model — a single
read-only tool call is within its ability. The escalation branch (14B for reliable
multi-step tool chains) lands in Phase 5/6; this is the seam it plugs into, so the
later change is config + one branch here, not a rewrite."""


class ModelRouter:
    def __init__(self, fast_model: str, escalation_model: str | None = None):
        self._fast = fast_model
        self._escalation = escalation_model

    def pick_model(self, message: str, *, needs_tools: bool) -> str:
        # Phase 3: no escalation yet. When multi-step chains appear, escalate here.
        return self._fast
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/daemon/llm/test_model_router.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/llm/model_router.py tests/daemon/llm/test_model_router.py
git commit -m "Add ModelRouter seam: fast model now, escalation later

This commit used N prompts."
```

---

### Task 6: Router integration — TOOL_HINT gate + tool-loop path

**Files:**
- Modify: `lumen/daemon/router.py`
- Modify: `lumen/daemon/__main__.py`
- Test: `tests/daemon/test_router.py` (append)

**Interfaces:**
- Consumes: `MCPBridge.ollama_tools`/`.call`/`.ensure_started` (Tasks 2–3), `OllamaClient.chat_with_tools` (Task 4), `ModelRouter.pick_model` (Task 5). A `tool_log` with `.write(name, arguments, ok, result, duration_ms)` arrives in Task 7 — in this task the router accepts `tool_log=None` and skips logging when absent.
- Produces: `TOOL_HINT` regex; `Router(llm, todos, *, bridge=None, model_router=None, tool_log=None, max_iterations=4)`. When `bridge` is set and `TOOL_HINT` matches, chat runs the tool loop and emits `{"tool_used": name}` before the answer chunk; otherwise the existing plain-chat path is unchanged. Adds a lazy `bridge.ensure_started()` hook: **connection is lazy**, so the bridge exposes `async ensure_started()` that connects on first use. To keep Task 3's `connect_servers` as the connector, wrap it: `__main__` passes a not-yet-connected `LazyBridge`.

**Note on lazy connect:** `connect_servers` (Task 3) does the actual connecting. Wrap it in a tiny lazy holder so the daemon spawns no subprocess until the first tool query:

- [ ] **Step 1: Add `LazyBridge` to `mcp_bridge.py`** and its test — append to `lumen/daemon/llm/mcp_bridge.py`:

```python
class LazyBridge:
    """Defers connect_servers until the first tool need (power discipline: an
    MCP-enabled daemon that never gets a tool question spawns nothing)."""

    def __init__(self, servers: list):
        self._servers = servers
        self._bridge: MCPBridge | None = None

    async def ensure_started(self) -> None:
        if self._bridge is None:
            self._bridge = await connect_servers(self._servers)

    def ollama_tools(self) -> list[dict]:
        return self._bridge.ollama_tools() if self._bridge else []

    async def call(self, name: str, arguments: dict) -> str:
        return await self._bridge.call(name, arguments)

    async def aclose(self) -> None:
        if self._bridge is not None:
            await self._bridge.aclose()
            self._bridge = None
```

Append to `tests/daemon/llm/test_mcp_bridge.py`:

```python
async def test_lazy_bridge_connects_once(monkeypatch):
    from lumen.daemon.llm import mcp_bridge as mod
    calls = {"n": 0}

    async def fake_connect(servers):
        calls["n"] += 1
        b = MCPBridge({}, {})
        await b.load_tools()
        return b

    monkeypatch.setattr(mod, "connect_servers", fake_connect)
    lazy = mod.LazyBridge([])
    assert lazy.ollama_tools() == []          # nothing before start
    await lazy.ensure_started()
    await lazy.ensure_started()
    assert calls["n"] == 1                     # connected exactly once
```

Run: `uv run pytest tests/daemon/llm/test_mcp_bridge.py -k lazy -v` → PASS.

- [ ] **Step 2: Write the failing router tests** — append to `tests/daemon/test_router.py`:

```python
import re

from lumen.daemon.llm.mcp_bridge import ToolCallError


class FakeBridge:
    def __init__(self, tools=(("list_directory", {}),), result="a.txt\nb.txt", fail=False):
        self._tools = [{"type": "function", "function": {"name": n}} for n, _ in tools]
        self._result = result
        self._fail = fail
        self.started = False
        self.calls = []

    async def ensure_started(self):
        self.started = True

    def ollama_tools(self):
        return self._tools

    async def call(self, name, args):
        self.calls.append((name, args))
        if self._fail:
            raise ToolCallError("boom")
        return self._result


class ToolLLM:
    """Fake LLM whose chat_with_tools invokes one tool then answers."""
    def __init__(self):
        self.model = None

    async def chat_with_tools(self, messages, tools, executor, *, model=None, max_iterations=4):
        yield {"tool_call": {"name": "list_directory", "arguments": {"path": "/n"}}}
        text = await executor("list_directory", {"path": "/n"})
        yield {"content": f"Files: {text}"}


class FakeModelRouter:
    def pick_model(self, message, *, needs_tools):
        return "fast"


def test_tool_hint_matches_lookup_phrases():
    from lumen.daemon.router import TOOL_HINT
    assert TOOL_HINT.search("what files are in my notes")
    assert TOOL_HINT.search("who wrote Dune")
    assert TOOL_HINT.search("look up the isbn")
    assert not TOOL_HINT.search("how are you today")


async def test_chat_runs_tool_loop_and_emits_tool_used():
    bridge = FakeBridge()
    router = Router(ToolLLM(), FakeStore(), bridge=bridge, model_router=FakeModelRouter())
    out = await collect(router, "chat", {"message": "what files are in /n"})
    assert bridge.started is True
    assert {"tool_used": "list_directory"} in out
    assert any(o.get("chunk", "").startswith("Files: a.txt") for o in out)
    assert out[-1] == {"done": True}


async def test_chat_without_bridge_uses_plain_path():
    llm = FakeLLM()
    out = await collect(Router(llm, FakeStore()), "chat", {"message": "who wrote Dune"})
    # no bridge → plain chat, no tool_used
    assert not any("tool_used" in o for o in out)
    assert out == [{"chunk": "a"}, {"chunk": "b"}, {"done": True}]


async def test_chat_non_lookup_skips_tools_even_with_bridge():
    bridge = FakeBridge()
    out = await collect(Router(FakeLLM(), FakeStore(), bridge=bridge, model_router=FakeModelRouter()),
                        "chat", {"message": "how are you today"})
    assert bridge.started is False           # gate missed → no connect, plain path
    assert out == [{"chunk": "a"}, {"chunk": "b"}, {"done": True}]
```

- [ ] **Step 3: Run to verify they fail**

Run: `uv run pytest tests/daemon/test_router.py -k "tool or bridge or hint" -v`
Expected: FAIL (`Router() got unexpected keyword 'bridge'`, no `TOOL_HINT`).

- [ ] **Step 4: Implement** — edit `lumen/daemon/router.py`. Add imports and the hint near the top:

```python
import time
```

```python
TOOL_HINT = re.compile(
    r"\b(look ?up|search|find|who wrote|author of|isbn|published|"
    r"books?|novels?|files?|folder|directory|notes|list .*files|what files)\b",
    re.IGNORECASE,
)
```

Change `Router.__init__`:

```python
    def __init__(self, llm, todos, *, bridge=None, model_router=None, tool_log=None,
                 max_iterations=4):
        self._llm = llm
        self._todos = todos
        self._bridge = bridge
        self._model_router = model_router
        self._tool_log = tool_log
        self._max_iterations = max_iterations
```

In `handle`, replace the `if type_ == "chat":` block body with a branch that tries the tool path first:

```python
        if type_ == "chat":
            message = payload.get("message", "")
            if self._bridge is not None and TOOL_HINT.search(message):
                async for ev in self._chat_with_tools(message):
                    yield ev
                return
            messages = []
            if TODO_HINT.search(message):
                messages.append({"role": "system",
                                 "content": todo_context(self._todos.open_todos(),
                                                         date.today())})
            messages.append({"role": "user", "content": message})
            try:
                async for chunk in self._llm.chat(messages):
                    yield {"chunk": chunk}
            except LLMUnavailable as e:
                yield {"error": str(e)}
                return
            yield {"done": True}
```

Add the tool-path helper method to `Router`:

```python
    async def _chat_with_tools(self, message: str):
        await self._bridge.ensure_started()
        tools = self._bridge.ollama_tools()
        if not tools:                      # no servers came up → fall back to plain chat
            try:
                async for chunk in self._llm.chat([{"role": "user", "content": message}]):
                    yield {"chunk": chunk}
            except LLMUnavailable as e:
                yield {"error": str(e)}
                return
            yield {"done": True}
            return

        async def executor(name, args):
            start = time.monotonic()
            try:
                text = await self._bridge.call(name, args)
                ok = True
            except ToolCallError as e:
                text, ok = f"tool error: {e}", False
            if self._tool_log is not None:
                self._tool_log.write(name, args, ok, text,
                                     int((time.monotonic() - start) * 1000))
            return text

        messages = []
        if TODO_HINT.search(message):
            messages.append({"role": "system",
                             "content": todo_context(self._todos.open_todos(), date.today())})
        messages.append({"role": "user", "content": message})
        model = self._model_router.pick_model(message, needs_tools=True) \
            if self._model_router else None
        try:
            async for ev in self._llm.chat_with_tools(
                messages, tools, executor, model=model, max_iterations=self._max_iterations):
                if "tool_call" in ev:
                    yield {"tool_used": ev["tool_call"]["name"]}
                elif ev.get("capped"):
                    yield {"chunk": "(stopped after several tool steps without a final answer)"}
                elif "content" in ev:
                    yield {"chunk": ev["content"]}
        except LLMUnavailable as e:
            yield {"error": str(e)}
            return
        yield {"done": True}
```

Add the import at the top of `router.py`:

```python
from lumen.daemon.llm.mcp_bridge import ToolCallError
```

- [ ] **Step 5: Wire `__main__.py`** — edit `lumen/daemon/__main__.py`:

```python
from lumen.daemon.llm.mcp_bridge import LazyBridge
from lumen.daemon.llm.model_router import ModelRouter
```

In `run()`, after building `llm` and before `IPCServer`:

```python
    bridge = LazyBridge(list(cfg.mcp.servers)) if cfg.mcp.enabled else None
    model_router = ModelRouter(cfg.model)
    router = Router(llm, TodoStore(conn), bridge=bridge, model_router=model_router,
                    max_iterations=cfg.mcp.max_iterations)
    server = IPCServer(cfg.socket_path, router)
```

And in the shutdown section, after `await server.stop()`:

```python
    if bridge is not None:
        await bridge.aclose()
```

- [ ] **Step 6: Run to verify pass**

Run: `uv run pytest tests/daemon/test_router.py -v`
Expected: PASS (existing plain-chat/todo tests unchanged + new tool tests).

- [ ] **Step 7: Commit**

```bash
git add lumen/daemon/router.py lumen/daemon/__main__.py lumen/daemon/llm/mcp_bridge.py tests/daemon/test_router.py tests/daemon/llm/test_mcp_bridge.py
git commit -m "Wire MCP tool loop into the chat router behind a heuristic gate

This commit used N prompts."
```

---

### Task 7: Tool-call log — the grounding-verification artifact

**Files:**
- Create: `lumen/daemon/llm/tool_log.py`
- Modify: `lumen/daemon/__main__.py` (pass the log into Router)
- Test: `tests/daemon/llm/test_tool_log.py` (new)

**Interfaces:**
- Consumes: `MCPConfig.log_path` (Task 1); Router's `tool_log` param + `.write(...)` call (Task 6).
- Produces: `class ToolLog(path: Path, excerpt_len: int = 500)` with `def write(self, tool: str, arguments: dict, ok: bool, result: str, duration_ms: int) -> None` — appends one JSON object per line: `{ts, tool, arguments, ok, result_excerpt, duration_ms}`. Creates the parent dir on first write; `result_excerpt` truncated to `excerpt_len`.

- [ ] **Step 1: Write the failing tests** — create `tests/daemon/llm/test_tool_log.py`:

```python
import json

from lumen.daemon.llm.tool_log import ToolLog


def test_write_appends_jsonl_line(tmp_path):
    p = tmp_path / "sub" / "tool-calls.jsonl"
    log = ToolLog(p)
    log.write("list_directory", {"path": "/n"}, True, "a.txt\nb.txt", 12)
    log.write("read_file", {"path": "/n/a.txt"}, True, "hello", 5)
    lines = p.read_text().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["tool"] == "list_directory"
    assert first["arguments"] == {"path": "/n"}
    assert first["ok"] is True
    assert first["result_excerpt"] == "a.txt\nb.txt"
    assert first["duration_ms"] == 12
    assert "ts" in first


def test_write_truncates_long_results(tmp_path):
    p = tmp_path / "tool-calls.jsonl"
    ToolLog(p, excerpt_len=10).write("x", {}, True, "y" * 5000, 1)
    rec = json.loads(p.read_text().splitlines()[0])
    assert len(rec["result_excerpt"]) == 10


def test_write_records_errors(tmp_path):
    p = tmp_path / "tool-calls.jsonl"
    ToolLog(p).write("read_file", {"path": "/x"}, False, "tool error: not found", 3)
    rec = json.loads(p.read_text().splitlines()[0])
    assert rec["ok"] is False
    assert "not found" in rec["result_excerpt"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/daemon/llm/test_tool_log.py -v`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implement** — create `lumen/daemon/llm/tool_log.py`:

```python
"""Append-only JSONL record of every MCP tool execution. This is the artifact the
Phase 3 success criterion checks answers against: one line per call, greppable."""

import json
from datetime import datetime
from pathlib import Path


class ToolLog:
    def __init__(self, path: Path, excerpt_len: int = 500):
        self._path = Path(path)
        self._excerpt_len = excerpt_len

    def write(self, tool: str, arguments: dict, ok: bool, result: str, duration_ms: int) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "tool": tool,
            "arguments": arguments,
            "ok": ok,
            "result_excerpt": (result or "")[: self._excerpt_len],
            "duration_ms": duration_ms,
        }
        with open(self._path, "a") as f:
            f.write(json.dumps(record) + "\n")
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/daemon/llm/test_tool_log.py -v`
Expected: PASS.

- [ ] **Step 5: Wire it into `__main__.py`** — edit `lumen/daemon/__main__.py`:

```python
from lumen.daemon.llm.tool_log import ToolLog
```

Change the router construction to build and pass the log when MCP is enabled:

```python
    bridge = LazyBridge(list(cfg.mcp.servers)) if cfg.mcp.enabled else None
    model_router = ModelRouter(cfg.model)
    tool_log = ToolLog(cfg.mcp.log_path) if cfg.mcp.enabled else None
    router = Router(llm, TodoStore(conn), bridge=bridge, model_router=model_router,
                    tool_log=tool_log, max_iterations=cfg.mcp.max_iterations)
```

- [ ] **Step 6: Verify the daemon still imports/starts**

Run: `uv run python -c "import lumen.daemon.__main__ as m; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 7: Commit**

```bash
git add lumen/daemon/llm/tool_log.py lumen/daemon/__main__.py tests/daemon/llm/test_tool_log.py
git commit -m "Add ToolLog JSONL writer and wire it into the tool loop

This commit used N prompts."
```

---

### Task 8: UI — surface the `tool_used` marker

**Files:**
- Modify: `lumen/ui/daemon_client.py`
- Modify: `lumen/ui/launcher.py`
- Test: `tests/ui/test_daemon_client.py` (append), `tests/ui/test_launcher.py` (append)

**Interfaces:**
- Consumes: the daemon's `{"tool_used": name}` IPC line (Task 6).
- Produces: `DaemonClient.tool_used = pyqtSignal(str)` emitted on a `tool_used` line; the launcher renders a muted `🔧 used <name>` line above the streamed answer, cleared on each new submit.

- [ ] **Step 1: Write the failing daemon_client test** — append to `tests/ui/test_daemon_client.py` (match the file's existing style for feeding lines into `_on_ready_read`; if it drives the socket, mirror that — otherwise call the parse path directly as below):

```python
def test_tool_used_line_emits_signal(qtbot):
    from lumen.ui.daemon_client import DaemonClient
    client = DaemonClient("/nonexistent.sock")
    seen = []
    client.tool_used.connect(seen.append)
    # feed a tool_used line through the same buffer path the socket uses
    client._buf = b'{"id": 1, "tool_used": "search_books"}\n'
    client._on_ready_read_from_buffer() if hasattr(client, "_on_ready_read_from_buffer") \
        else _drain(client)
    assert seen == ["search_books"]


def _drain(client):
    # replicate _on_ready_read's line loop over the pre-seeded buffer (no socket)
    import json
    while b"\n" in client._buf:
        raw, client._buf = client._buf.split(b"\n", 1)
        msg = json.loads(raw)
        if "tool_used" in msg:
            client.tool_used.emit(msg["tool_used"])
```

> Implementer note: prefer refactoring `_on_ready_read` so the line-handling loop is a
> separate method you can call in the test with a seeded `_buf` (no live socket). If you do,
> drop `_drain` and call that method directly. Keep the socket read in `_on_ready_read`.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/ui/test_daemon_client.py -k tool_used -v`
Expected: FAIL (no `tool_used` attribute).

- [ ] **Step 3: Implement in `daemon_client.py`** — add the signal beside the others:

```python
    tool_used = pyqtSignal(str)
```

Add a branch in `_on_ready_read`'s per-message handling, **before** the `chunk` branch (a `tool_used` line has none of error/result/done/chunk keys):

```python
            elif "tool_used" in msg:
                self.tool_used.emit(msg["tool_used"])
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/ui/test_daemon_client.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing launcher test** — append to `tests/ui/test_launcher.py`:

```python
def test_tool_marker_shown_then_cleared_on_submit(qtbot):
    from lumen.ui.launcher import LauncherScreen

    class StubClient:
        from PyQt6.QtCore import pyqtSignal  # noqa
    # Use the real DaemonClient signal surface via a lightweight stub:
    from lumen.ui.daemon_client import DaemonClient
    client = DaemonClient("/nonexistent.sock")
    screen = LauncherScreen(client)
    qtbot.addWidget(screen)

    client.tool_used.emit("search_books")
    assert "search_books" in screen.tool_marker.text()
    assert screen.tool_marker.isVisible()

    screen.input.setText("next question")
    screen._submit()
    assert not screen.tool_marker.isVisible()   # cleared for the new answer
```

- [ ] **Step 6: Run to verify it fails**

Run: `uv run pytest tests/ui/test_launcher.py -k tool_marker -v`
Expected: FAIL (no `tool_marker`).

- [ ] **Step 7: Implement in `launcher.py`** — in `LauncherScreen.__init__`, connect the signal:

```python
        client.tool_used.connect(self._on_tool_used)
```

Add a marker label just above `self.response` (after `self.status` is added to `root`):

```python
        self.tool_marker = label("", "faint")
        self.tool_marker.hide()
        root.addWidget(self.tool_marker)
```

Add the handler and clear it on submit:

```python
    def _on_tool_used(self, name: str) -> None:
        self.tool_marker.setText(f"🔧 used {name}")
        self.tool_marker.show()
```

In `_submit`, alongside the other resets (`self.response.clear()` etc.), add:

```python
        self.tool_marker.hide()
```

- [ ] **Step 8: Run to verify pass**

Run: `uv run pytest tests/ui/test_launcher.py -v`
Expected: PASS (existing launcher tests + new).

- [ ] **Step 9: Commit**

```bash
git add lumen/ui/daemon_client.py lumen/ui/launcher.py tests/ui/test_daemon_client.py tests/ui/test_launcher.py
git commit -m "Surface a 'used <tool>' marker in the launcher

This commit used N prompts."
```

---

### Task 9: Custom Open Library MCP server

**Files:**
- Create: `lumen/mcp_servers/__init__.py`
- Create: `lumen/mcp_servers/openlibrary.py`
- Test: `tests/mcp_servers/__init__.py` (new), `tests/mcp_servers/test_openlibrary.py` (new)

**Interfaces:**
- Consumes: `mcp` (Task 1), `httpx` (existing).
- Produces: a stdio MCP server run as `python -m lumen.mcp_servers.openlibrary`, exposing two read-only tools: `search_books(query: str, limit: int = 5) -> str` and `get_book(olid_or_isbn: str) -> str`. The lookup logic lives in plain async functions (`_search`, `_get`) that take an `httpx.AsyncClient`, so they test without a live network or a running server.

- [ ] **Step 1: Write the failing tests** — create `tests/mcp_servers/__init__.py` (empty) and `tests/mcp_servers/test_openlibrary.py`:

```python
import httpx

from lumen.mcp_servers.openlibrary import _get, _search


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://openlibrary.org")


async def test_search_returns_grounded_lines():
    def handler(request):
        assert "/search.json" in request.url.path
        return httpx.Response(200, json={"docs": [
            {"title": "Dune", "author_name": ["Frank Herbert"], "first_publish_year": 1965,
             "key": "/works/OL893415W", "isbn": ["9780441172719"]},
        ]})

    out = await _search(_client(handler), "dune", 5)
    assert "Dune" in out and "Frank Herbert" in out and "1965" in out


async def test_search_handles_no_results():
    def handler(request):
        return httpx.Response(200, json={"docs": []})

    assert "no results" in (await _search(_client(handler), "zzzz", 5)).lower()


async def test_search_degrades_on_http_error():
    def handler(request):
        raise httpx.ConnectError("down")

    assert "look up" in (await _search(_client(handler), "dune", 5)).lower()


async def test_get_book_by_key():
    def handler(request):
        return httpx.Response(200, json={"title": "Dune",
            "description": "Desert planet.", "first_publish_date": "1965"})

    out = await _get(_client(handler), "/works/OL893415W")
    assert "Dune" in out
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/mcp_servers/test_openlibrary.py -v`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implement** — create `lumen/mcp_servers/__init__.py` (empty) and `lumen/mcp_servers/openlibrary.py`:

```python
"""Thin read-only Open Library MCP server (no API key). The reusable 'custom MCP
server' template for the project — Phase 4's book catalog and later lookups follow
this shape. Run: python -m lumen.mcp_servers.openlibrary"""

import httpx
from mcp.server.fastmcp import FastMCP

BASE_URL = "https://openlibrary.org"
TIMEOUT = httpx.Timeout(connect=5.0, read=8.0, write=5.0, pool=5.0)

mcp = FastMCP("openlibrary")


async def _search(client: httpx.AsyncClient, query: str, limit: int) -> str:
    try:
        resp = await client.get("/search.json",
                                params={"q": query, "limit": limit,
                                        "fields": "title,author_name,first_publish_year,key,isbn"})
        resp.raise_for_status()
        docs = resp.json().get("docs", [])
    except httpx.HTTPError:
        return "Couldn't look that up (Open Library request failed)."
    if not docs:
        return f"No results for {query!r}."
    lines = []
    for d in docs[:limit]:
        authors = ", ".join(d.get("author_name", []) or ["unknown author"])
        year = d.get("first_publish_year", "?")
        isbn = (d.get("isbn") or [None])[0]
        key = d.get("key", "")
        lines.append(f"- {d.get('title', 'Untitled')} — {authors} ({year})"
                     f"{f', ISBN {isbn}' if isbn else ''} [{key}]")
    return "\n".join(lines)


async def _get(client: httpx.AsyncClient, olid_or_isbn: str) -> str:
    path = olid_or_isbn if olid_or_isbn.startswith("/") else f"/works/{olid_or_isbn}"
    try:
        resp = await client.get(f"{path}.json")
        resp.raise_for_status()
        d = resp.json()
    except httpx.HTTPError:
        return "Couldn't look that up (Open Library request failed)."
    desc = d.get("description")
    if isinstance(desc, dict):
        desc = desc.get("value", "")
    return (f"{d.get('title', 'Untitled')} "
            f"({d.get('first_publish_date', '?')})\n{desc or 'No description.'}")


@mcp.tool()
async def search_books(query: str, limit: int = 5) -> str:
    """Search Open Library for books by title/author/keyword. Returns real titles,
    authors, publish years, and ISBNs — use this instead of guessing book facts."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
        return await _search(client, query, limit)


@mcp.tool()
async def get_book(olid_or_isbn: str) -> str:
    """Fetch details for one book by Open Library work key (e.g. /works/OL...W) or ISBN."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
        return await _get(client, olid_or_isbn)


if __name__ == "__main__":
    mcp.run()
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/mcp_servers/test_openlibrary.py -v`
Expected: PASS.

- [ ] **Step 5: Verify the server starts as a module** (it should block waiting on stdio; a 1s timeout that does NOT immediately error means it launched)

Run: `timeout 1 uv run python -m lumen.mcp_servers.openlibrary; test $? -eq 124 && echo "started-ok"`
Expected: prints `started-ok` (124 = timed out while running, i.e. it launched and waited for stdio).

- [ ] **Step 6: Commit**

```bash
git add lumen/mcp_servers/ tests/mcp_servers/
git commit -m "Add read-only Open Library MCP server (custom-server template)

This commit used N prompts."
```

---

### Task 10: End-to-end verification + doc/skill updates

**Files:**
- Modify: `.claude/skills/mcp-integration.md`, `.claude/skills/llm-serving.md`, `.claude/skills/development-plan.md`
- No new tests (full suite must be green; manual hardware verification).

**Interfaces:**
- Consumes: everything.

- [ ] **Step 1: Full test suite green**

Run: `uv run pytest -q`
Expected: all pass (no regressions in existing daemon/UI tests).

- [ ] **Step 2: Live end-to-end — filesystem server.** Prereqs: `ollama serve` running with `qwen3:4b-instruct` pulled; node/`npx` available. Create a real `config.toml` (copy `config.example.toml`, uncomment `[mcp]` + the `fs` server, point it at a folder with 2–3 known files). Start the daemon (`uv run lumen-daemon`), then the UI (`uv run lumen-ui`), and ask: *"what files are in my notes folder?"*
Expected: the answer lists the actual files; the launcher shows `🔧 used list_directory` (or `read_directory`); `tail -1 ~/.local/state/lumen/tool-calls.jsonl` shows the matching tool call and result. **Success criterion #1 + #2 for filesystem.**

- [ ] **Step 3: Live end-to-end — Open Library.** Add the `books` server block to `config.toml`, restart the daemon, and ask: *"who wrote Dune and what year was it published?"*
Expected: the answer's author/year matches the `search_books` result recorded in `tool-calls.jsonl`. **Success criterion for the custom server / Phase 4 on-ramp.**

- [ ] **Step 4: Record decisions in the skills** — update:
  - `mcp-integration.md`: note Approach A (option 3) was chosen and why; the read-only allowlist convention; the `lumen/mcp_servers/` custom-server template; non-streamed tool turns.
  - `llm-serving.md`: `qwen3:4b-instruct` handles single tool calls; 14B escalation still deferred; MCP subprocesses don't affect idle-unload.
  - `development-plan.md`: mark Phase 3 complete (matching how Phases 1–2 were closed).

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/mcp-integration.md .claude/skills/llm-serving.md .claude/skills/development-plan.md
git commit -m "Record Phase 3 decisions; mark MCP plumbing complete

This commit used N prompts."
```

---

## Self-review notes (author)

- **Spec coverage:** config (T1), bridge+conversion+allowlist (T2), stdio connect (T3), tool loop (T4), model_router seam (T5), router gate + tool_used (T6), JSONL log (T7), launcher marker (T8), Open Library server (T9), verification + doc updates (T10). All spec sections mapped.
- **Deliberate deviation from spec:** the tool-call log drops `req_id` correlation (the router never receives the IPC id; threading it would ripple through every fake router). Correlation is by `ts` + result content, which satisfies the verification criterion. Noted in the spec's log section too.
- **Read-only guarantee:** enforced by the config allowlist in T1 + the filtering in T2's `load_tools`; T10 Step 2 points the FS server at read tools only.
- **Back-compat:** `Router` gains keyword-only params defaulting to `None`, so existing `Router(FakeLLM(), FakeStore())` call sites and tests are unaffected; the plain-chat path is byte-for-byte unchanged when `bridge is None`.
- **Env prerequisites for live steps only** (not unit tests): Ollama + model, node/`npx`. Unit tests use fakes/`MockTransport` and the in-memory `mcp` paths, so CI/subagents need no network except `uv add mcp` in T1.
