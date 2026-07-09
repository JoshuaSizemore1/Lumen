"""MCP → Ollama bridge. Converts MCP tool schemas to Ollama's `tools` format,
routes tool calls to the owning server, and flattens results to text. The stdio
subprocess wiring lives in connect_servers (Task 3); MCPBridge itself is pure
aggregation over injected clients so it unit-tests without a subprocess."""

import logging
from collections import Counter
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from lumen.daemon.config import MCPServerConfig

log = logging.getLogger(__name__)


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
    def __init__(self, clients: dict, allowlists: dict, stack=None):
        self._clients = clients                 # server_name -> client
        self._allow = allowlists                # server_name -> tuple[str,...] | None
        self._schemas: list[dict] = []          # Ollama tool schemas (namespaced names)
        self._registry: dict[str, tuple] = {}   # exposed_name -> (server_name, original_name)
        self._stack = stack                     # AsyncExitStack owning subprocess lifetime, if any

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

    async def aclose(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None


def server_params(cfg: MCPServerConfig) -> StdioServerParameters:
    return StdioServerParameters(command=cfg.command, args=list(cfg.args))


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
    bridge = MCPBridge(clients, allowlists, stack=stack)
    try:
        await bridge.load_tools()
    except Exception:
        await stack.aclose()
        raise
    return bridge


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
