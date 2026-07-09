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
