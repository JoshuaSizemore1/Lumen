"""The prompt surface the model actually sees — derived, never hand-copied.

The 2026-07-18 local-inference eval reported its tool-accuracy headline against
a hand-written *copy* of Lumen's tool schemas (bench/lumen_tools.py). That copy
had drifted: it declared four todo tools and a `lookup_book` that production
does not have, and a system prompt that was not IDENTITY. The headline
therefore described a tool surface that never shipped.

This module removes the possibility of that happening again. Every description
here is read from the live FastMCP server objects, converted with the daemon's
own `tool_to_ollama_schema`, and filtered exactly as the runtime filters: the
per-server `tools` allowlist from config.toml first (LazyBridge.load_tools),
then WRITE_TOOLS (the router) — so a description edit lands here with no second
copy to update.

The allowlist matters more than it looks. A tool can exist on the server and
still be invisible to the model because config.toml doesn't name it; grading it
here would be the same class of lie as the hand-written copy, just relocated.
`test_config_allowlist_matches_the_servers` keeps the two honest.

The `todos` group is not an MCP server at all — its tools run in-process
(daemon/local_tools.py) because the daemon owns the SQLite handle. They are
read from that module here for the same reason: the model sees them, so the
eval must too, and there must be no second copy.

Scope: the three in-repo Python MCP servers plus the local todo tools. The
`fs` group is
@modelcontextprotocol/server-filesystem — an external npx process whose 11 tool
descriptions we do not author and cannot edit; grounding it is fs_context's job,
not a docstring's. Introspecting it would mean spawning a subprocess, which a
unit test should not do.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

from lumen.daemon import local_tools
from lumen.daemon.config import load_config
from lumen.daemon.llm.mcp_bridge import tool_to_ollama_schema
from lumen.daemon.router import IDENTITY, WRITE_TOOLS
from lumen.mcp_servers import gcal, mail, openlibrary

__all__ = ["IDENTITY", "LOCAL_SERVERS", "EXTERNAL_GROUPS", "owned_descriptions",
           "allowlists", "tool_schemas", "tool_names"]

# group name (= MCP server name in config.toml) -> module exposing `mcp`
LOCAL_SERVERS = {"gcal": gcal, "mail": mail, "books": openlibrary}

# Groups whose descriptions are third-party. Named so a reader knows the
# omission is deliberate, not an oversight.
EXTERNAL_GROUPS = frozenset({"fs"})


async def _load() -> dict[str, tuple[tuple[str, str, dict], ...]]:
    out = {}
    for group, module in LOCAL_SERVERS.items():
        out[group] = tuple((t.name, t.description or "", t.inputSchema)
                           for t in await module.mcp.list_tools())
    return out


@lru_cache(maxsize=1)
def _raw() -> dict[str, tuple[tuple[str, str, dict], ...]]:
    """group -> ((name, description, input_schema), ...) straight off the server.

    Callable from sync tests and from inside a running loop (the live eval is
    async), so the coroutine goes to a worker thread when a loop already owns
    this one. Cached: listing tools is pure introspection, not I/O.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_load())
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, _load()).result()


def _local_schemas() -> list[dict]:
    """The in-process todo tools, already in Ollama schema shape. No allowlist
    and no WRITE_TOOLS filter applies: the router attaches them whole."""
    return list(local_tools.TODO_TOOLS)


def owned_descriptions() -> dict[str, str]:
    """Every tool description Lumen authors, keyed by tool name — including the
    write tools, which never reach the model but are still ours to keep honest."""
    out = {name: desc for tools in _raw().values() for name, desc, _ in tools}
    out.update({t["function"]["name"]: t["function"]["description"]
                for t in _local_schemas()})
    return out


@lru_cache(maxsize=1)
def allowlists() -> dict[str, frozenset[str] | None]:
    """group -> the config.toml `tools` allowlist, or None for expose-all.
    Mirrors `LazyBridge.load_tools`, where `allow is None` means no filter."""
    return {s.name: (frozenset(s.tools) if s.tools is not None else None)
            for s in load_config().mcp.servers}


def tool_schemas(groups=None) -> list[dict]:
    """Ollama tool schemas exactly as `_chat_with_tools` would assemble them:
    the named groups' tools, minus anything config.toml's allowlist withholds,
    minus WRITE_TOOLS (create_event / delete_event are daemon-gated behind the
    confirm dialog and never offered to the model)."""
    wanted = set(LOCAL_SERVERS) if groups is None else (set(groups) & set(LOCAL_SERVERS))
    allow = allowlists()
    out = [tool_to_ollama_schema(name, desc, schema)
           for group in sorted(wanted)
           for name, desc, schema in _raw()[group]
           if name not in WRITE_TOOLS
           and (allow.get(group) is None or name in allow[group])]
    if groups is None or "todos" in set(groups):
        out += _local_schemas()
    return out


def tool_names(groups=None) -> set[str]:
    return {s["function"]["name"] for s in tool_schemas(groups)}
