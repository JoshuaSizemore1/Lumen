import pytest

from lumen.daemon.config import MCPServerConfig
from lumen.daemon.llm.mcp_bridge import (
    MCPBridge, ToolCallError, flatten_content, server_params, tool_to_ollama_schema,
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


async def test_ollama_tools_filtered_by_server():
    fs = FakeClient([T("read_file"), T("list_directory")])
    mail = FakeClient([T("search_email"), T("get_email")])
    bridge = MCPBridge({"fs": fs, "mail": mail}, {"fs": None, "mail": None})
    await bridge.load_tools()
    names = {t["function"]["name"] for t in bridge.ollama_tools(servers={"mail"})}
    assert names == {"search_email", "get_email"}
    assert len(bridge.ollama_tools()) == 4                 # None keeps everything
    assert bridge.ollama_tools(servers=set()) == []


async def test_server_filter_uses_registry_not_name_prefix():
    # Names are only namespaced on collision — filtering must resolve the
    # owning server through the registry, never by splitting the name.
    a, b = FakeClient([T("search")]), FakeClient([T("search")])
    bridge = MCPBridge({"fs": a, "books": b}, {"fs": None, "books": None})
    await bridge.load_tools()
    names = {t["function"]["name"] for t in bridge.ollama_tools(servers={"books"})}
    assert names == {"books__search"}


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


def test_server_params_builds_stdio_command():
    cfg = MCPServerConfig("fs", "npx", ["-y", "pkg", "~/notes"], ("read_file",))
    p = server_params(cfg)
    assert p.command == "npx"
    assert p.args == ["-y", "pkg", "~/notes"]


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
    await lazy.aclose()


async def test_lazy_bridge_concurrent_ensure_started_connects_once(monkeypatch):
    import asyncio
    from lumen.daemon.llm import mcp_bridge as mod
    calls = {"n": 0}

    async def fake_connect(servers):
        calls["n"] += 1
        await asyncio.sleep(0)          # yield so a second task can interleave
        b = MCPBridge({}, {})
        await b.load_tools()
        return b

    monkeypatch.setattr(mod, "connect_servers", fake_connect)
    lazy = mod.LazyBridge([])
    await asyncio.gather(lazy.ensure_started(), lazy.ensure_started())
    assert calls["n"] == 1              # lock prevents a double-connect
    await lazy.aclose()


async def test_lazy_bridge_closes_in_owner_task(monkeypatch):
    import asyncio
    from lumen.daemon.llm import mcp_bridge as mod
    tasks = {}

    class TaskRecordingBridge(MCPBridge):
        async def aclose(self):
            tasks["close"] = asyncio.current_task()

    async def fake_connect(servers):
        tasks["connect"] = asyncio.current_task()
        b = TaskRecordingBridge({}, {})
        await b.load_tools()
        return b

    monkeypatch.setattr(mod, "connect_servers", fake_connect)
    lazy = mod.LazyBridge([])

    async def handler():          # simulates an IPC connection handler task
        await lazy.ensure_started()

    await asyncio.create_task(handler())
    await lazy.aclose()           # from the "main" task, like daemon shutdown
    assert tasks["connect"] is tasks["close"]              # same owner task
    assert tasks["connect"] is not asyncio.current_task()  # not the caller


async def test_lazy_bridge_aclose_survives_teardown_failure(monkeypatch):
    from lumen.daemon.llm import mcp_bridge as mod

    class ExplodingCloseBridge(MCPBridge):
        async def aclose(self):
            raise RuntimeError("anyio teardown burp")

    async def fake_connect(servers):
        b = ExplodingCloseBridge({}, {})
        await b.load_tools()
        return b

    monkeypatch.setattr(mod, "connect_servers", fake_connect)
    lazy = mod.LazyBridge([])
    await lazy.ensure_started()
    await lazy.aclose()          # must not raise — daemon shutdown stays clean
    assert lazy.ollama_tools() == []


async def test_lazy_bridge_connect_timeout_raises_friendly_error(monkeypatch):
    import asyncio
    from lumen.daemon.llm import mcp_bridge as mod

    async def hung_connect(servers):
        await asyncio.sleep(3600)

    monkeypatch.setattr(mod, "connect_servers", hung_connect)
    monkeypatch.setattr(mod, "MCP_CONNECT_TIMEOUT_S", 0.05)
    lazy = mod.LazyBridge([])
    with pytest.raises(ToolCallError):
        await lazy.ensure_started()
    await lazy.aclose()          # owner was cancelled; aclose is a clean no-op


async def test_lazy_bridge_connect_failure_raises_and_allows_retry(monkeypatch):
    from lumen.daemon.llm import mcp_bridge as mod
    attempts = {"n": 0}

    async def flaky_connect(servers):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("first connect fails")
        b = MCPBridge({}, {})
        await b.load_tools()
        return b

    monkeypatch.setattr(mod, "connect_servers", flaky_connect)
    lazy = mod.LazyBridge([])
    with pytest.raises(RuntimeError):
        await lazy.ensure_started()
    await lazy.ensure_started()   # retry succeeds
    assert attempts["n"] == 2
    await lazy.aclose()
