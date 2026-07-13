# Skill: Email Menu (full inbox view/manage/send)

## Storage
Local SQLite, a full mirror (subject/body/snippet/labels, not just metadata) since the menu needs to browse/search actual content. *As-built (2026-07-12): the separate lightweight metadata cache this section originally called for was never built — the dashboard reads this same mirror. See the deviation note in `email-integration.md`.*
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
  **As built (Phase 7, 2026-07-13):** the editable compose popup *is* the
  confirmation — the user is looking at the exact recipients/body and clicks
  Send; no second dialog. Chat-driven drafts open the same popup pre-filled
  (`compose_request` over IPC; the turn awaits `compose.response`), the Mail
  screen's Compose/Reply buttons open it locally and send via `emails.send`.
  Sending needed no new scope — `gmail.modify` already authorizes it.
- Read/archive/label actions are also writes — confirm before executing, same as send (even though these feel low-stakes, consistency matters more than shaving a click).

## Privacy note
This table is a much fuller mirror of your inbox than the metadata-only cache used for dashboard summaries — worth encrypting at rest or at minimum locking down file permissions on the SQLite file, given the LLM (and anything else on the machine) can read it directly.

## What NOT to do
- Don't run the bulk historical sync through the MCP tool-calling loop — that's an LLM sitting in a pagination loop for no reason. Bulk sync is a plain background job; MCP is for the LLM's on-demand decisions.
- Don't re-list and diff against the DB on every open — use the History API delta.
- Don't pull unbounded history on first run without a resumable pagination state.

## Durable gotchas (built 2026-07-12, Phase 6 — `daemon/connectors/email_menu.py`)
- **UPSERT, never `INSERT OR REPLACE`, on `emails`.** `REPLACE` deletes-then-reinserts under the hood, which bypasses the delete trigger backing the external-content FTS5 index and silently corrupts search. Use `INSERT ... ON CONFLICT(id) DO UPDATE SET ...` instead — `EmailStore.upsert` is the reference.
- **`historyId` is captured from `getProfile` BEFORE the bulk listing starts**, not after. Anything that changes mid-pull is then covered by the first incremental sync instead of falling into a gap.
- **Bulk-pending sentinel**: the pagination cursor is set to an empty string the moment a bulk run starts (before page 1 is even attempted), not left unset. That way a page-1 failure still resumes as a bulk run on the next poll instead of falling through to `_incremental` with no cursor and no backlog.
- **Re-baseline prunes under a run-id.** When the History API 404s (expired `historyId`, e.g. after a long time offline), the sync re-baselines with a fresh bulk pull tagged with a run id; rows in the bounded window not touched by that run get pruned (`prune_not_seen`) so mail that disappeared upstream during the gap doesn't linger as a zombie row.
- **Incremental adds are fetched per-id**, not batched: a 404 on an individual id (message vanished upstream between the delta and the fetch) is skipped; any other fetch error leaves `historyId` unadvanced so the identical window retries on the next poll instead of silently losing messages.
- **The dashboard reads the mirror directly** — no second lightweight metadata cache. See `email-integration.md` for the deviation and why.
- **The mail MCP server (`lumen/mcp_servers/mail.py`) is read-only by design** — `search_email`/`get_email` only, over a read-only SQLite URI handle (`mode=ro`) on the mirror. Archive/mark-read are UI-confirmed one-shots through the router, never model-initiated tool calls.
- **`emails_fts` is keyed to the implicit rowid of `emails`** (TEXT primary key); `VACUUM` renumbers implicit rowids and silently desyncs the index — if the DB is ever vacuumed, run `INSERT INTO emails_fts(emails_fts) VALUES('rebuild');` afterward.

## Decided gates (2026-07-12)
- Bulk sync window: 6 months, configurable via `config.toml`'s `gmail_window_months` (default 6).
- Browsing the mail menu never changes read state — opening/viewing a message does not implicitly mark it read; only the explicit mark-read action does.
- v1 manage actions are archive and mark read/unread only. Label management and delete are out of scope for this phase.
