# Skill: MCP Integration

## Why MCP instead of hand-rolled connectors
MCP (Model Context Protocol) standardizes how an LLM discovers and calls external tools. Rather than writing bespoke Gmail/Calendar API wrappers, the daemon connects to MCP servers that already expose these as tools, and the LLM calls them directly through the router.

## Servers to use
- **Gmail + Calendar**: `mcp-google-workspace` (community, runs locally via `npx`, single OAuth setup covers both) is the simplest self-hosted path. Google's official remote servers (`gmailmcp.googleapis.com`, `calendarmcp.googleapis.com`) are the alternative if a full Google Cloud Console OAuth consent-screen setup is preferred — more enterprise-flavored than a single-user app needs, but an option.
- **Research/lookup**: Brave Search MCP server (requires a free-tier API key) for "look this up" queries. A DuckDuckGo MCP server is a no-signup alternative if that's preferred.
- **Books lookup**: Open Library API (no key required) for grounding book recommendations — see `book-catalog.md`. Wrap it as a thin custom MCP server to keep it consistent with the rest of the integration pattern.

## Bridging local models to MCP
Ollama has no native MCP client support (as of April 2026, still an open feature request) — pick one:
1. **Swap runtime to llama.cpp's `llama-server`** — merged native MCP client support in March 2026, no separate bridge process needed. Simplest fit for this project's single-daemon design.
2. **Keep Ollama + a bridge** — `mcphost` (Go) or `ollmcp` (Python, TUI) translate MCP tool schemas into Ollama's function-calling format.
3. **Build the bridge into `daemon/llm/`** — MCP Python SDK + Ollama Python client, logic living where the router already lives. Most consistent with this project's "everything routes through daemon/llm/" rule.

Recommendation: start with (1) if open to the runtime swap — fewer moving parts. Otherwise (3).

**Decision (2026-07-09, Phase 3 verified):** Option 3, chosen for Phase 3. The bridge lives in `daemon/llm/mcp_bridge.py`: `MCPBridge` converts MCP tool schemas to Ollama's native `tools` API and routes calls back to the owning server; `LazyBridge` defers `connect_servers` (stdio subprocess spawn) until the first tool-shaped request, so an MCP-enabled daemon that never gets asked a lookup question spawns nothing. `daemon/llm/client.py`'s `chat_with_tools` drives the agentic loop with non-streamed turns (`stream: false` on `/api/chat`) — needed because tool_calls only arrive as a complete JSON object, not incrementally. Live end-to-end run (filesystem + Open Library, both single-tool-call) confirmed the loop works against real Ollama; see `llm-serving.md` for the model-behavior notes and `development-plan.md` for the Phase 3 close-out.

Read-only allowlist convention: each `[[mcp.servers]]` block's `tools` = the exposed set; `MCPBridge.load_tools()` drops any tool name not listed (omit `tools` to expose everything — avoid this for write-capable servers). One gotcha found during verification: if the allowlist excludes discovery tools like the filesystem server's `list_allowed_directories`, the model has no way to learn the real absolute root and guesses a relative path, which fails with ENOENT — include any "tell me the accessible paths" tool alongside the read tools.

`lumen/mcp_servers/` is the template location for custom in-repo servers — `openlibrary.py` (built on `mcp.server.fastmcp.FastMCP`) is the reference shape for Phase 4's book catalog and later lookups: thin `@mcp.tool()` functions, no API key, graceful-degradation return strings on HTTP failure instead of raising.

## Model size reality check
Tool-calling reliability scales with model size. As of 2026, community consensus is 14B parameters minimum for anything beyond a single obvious tool call, and 32B+ for reliable multi-step chains (e.g. "check my calendar, then draft a reply referencing tomorrow's meeting"). This is heavier than the 3B–7B estimate in `llm-serving.md` — benchmark cold-start and thermal behavior on the actual hardware before committing. A workable middle ground: keep a small (3B) model as the default for simple queries, and only load a 14B+ model when the router detects a task needs chained tool calls.

## Safety
MCP doesn't change the write-confirmation rule already in CLAUDE.md — any tool call that sends, creates, deletes, or modifies still surfaces a confirmation in the UI before executing, regardless of which MCP server it's routed through.
