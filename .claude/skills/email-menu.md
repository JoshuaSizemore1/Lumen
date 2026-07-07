# Skill: Email Menu (full inbox view/manage/send)

## Storage
Local SQLite, separate from the lightweight metadata cache in `email-integration.md` — this is a fuller mirror since the menu needs to browse/search actual content.
```sql
CREATE TABLE emails (
    id TEXT PRIMARY KEY,       -- Gmail message id
    thread_id TEXT,
    sender TEXT,
    recipients TEXT,
    subject TEXT,
    body TEXT,
    snippet TEXT,
    labels TEXT,                -- comma-separated
    received_at TEXT,
    is_read INTEGER
);
CREATE TABLE sync_state (
    key TEXT PRIMARY KEY,       -- e.g. 'gmail_history_id'
    value TEXT
);
```
Use SQLite FTS5 (or an equivalent full-text index) over `subject`/`body`/`snippet` so the email menu's search is fast without hitting the Gmail API on every keystroke.

## Sync strategy — two separate paths, not one
1. **Bulk sync worker** (background, no LLM involved): a plain script/service using the Gmail API client directly.
   - First run: bounded initial pull (default last 6-12 months, configurable), paginated, with pagination state persisted so an interrupted sync resumes rather than restarting.
   - After first run: uses Gmail's **History API** with the stored `historyId` to pull only what changed (new mail, deletions, label/read-state changes) — not a naive "list recent and diff against DB" approach, which wastes API calls and misses non-new changes.
   - Runs on app open, on manual refresh, and optionally on the same timer as the lightweight metadata sync.
2. **Conversational/LLM path** (via MCP): the router queries the **local DB** for anything historical or search-like ("find that email from X about Y") — fast, offline, no quota cost. It only reaches the live Gmail MCP connector for sending, or for anything time-sensitive enough that the local cache might be stale.

## Email menu UI behavior
- Full list/search view, not just today's unread — this is the "manage it yourself" surface, same philosophy as the todo manager.
- Compose/send goes through the standard write-confirmation flow from CLAUDE.md.
- Read/archive/label actions are also writes — confirm before executing, same as send (even though these feel low-stakes, consistency matters more than shaving a click).

## Privacy note
This table is a much fuller mirror of your inbox than the metadata-only cache used for dashboard summaries — worth encrypting at rest or at minimum locking down file permissions on the SQLite file, given the LLM (and anything else on the machine) can read it directly.

## What NOT to do
- Don't run the bulk historical sync through the MCP tool-calling loop — that's an LLM sitting in a pagination loop for no reason. Bulk sync is a plain background job; MCP is for the LLM's on-demand decisions.
- Don't re-list and diff against the DB on every open — use the History API delta.
- Don't pull unbounded history on first run without a resumable pagination state.
