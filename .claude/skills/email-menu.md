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
- Write actions are tiered (revised 2026-07-15): **archive** still confirms
  before executing; **read-state** changes (mark read/unread, dwell auto-read)
  are ungated — reversible, low-stakes, and the dwell timer would make a
  confirm dialog absurd; **labeling** is pre-authorized either at rule-creation
  time (the confirm covers all future applications) or by the explicit tap on
  a suggestion chip.

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

## Decided gates (2026-07-12, revised 2026-07-15)
- Bulk sync window: 6 months, configurable via `config.toml`'s `gmail_window_months` (default 6).
- ~~Browsing the mail menu never changes read state~~ **Reversed 2026-07-15,
  simplified 2026-07-16** (user decisions): opening a message marks it read,
  Gmail-style — the row flips locally at once, propagated to Gmail via a
  silent idempotent `emails.auto_read` one-shot. The 1s dwell timer was
  removed 2026-07-16: the list has no keyboard browsing, so every selection
  is a deliberate click and the delay only read as "auto-read is broken".
  Programmatic selection (`_set_mails` picking the first row) never marks
  anything. Explicit mark read/unread is also ungated — consistency with the
  silent path.
- Archive still confirms. Delete is still out of scope.
- **Labeling = moving** (Gmail semantics the user chose): applying a label
  also removes `INBOX`, locally and upstream. There is no keep-in-inbox+label
  variant.

## Labels & rules (built 2026-07-15)
- **`gmail_labels`** table mirrors the Gmail label list (id↔name, type);
  refreshed at the top of every sync pass (failures non-fatal). Rows store
  label **ids** in `emails.labels`; the UI receives resolved `label_names`
  (user labels only).
- **`mail_rules`** table: label + any-of matchers (`from_addrs`, `domains`,
  `subject_kw`, `body_kw` as JSON lists). Deterministic matching only
  (`daemon/connectors/mail_rules.py`) — exact sender, domain suffix,
  casefolded substring on subject/body.
- **Rules run in the incremental sync path only** — applied to newly arrived
  INBOX mail after upsert, never during bulk/re-baseline pulls, and the poller
  NEVER wakes the LLM (power/thermal constraint). Rule application is
  pre-authorized at creation: the confirm dialog (⚑ "Create mail rule") is the
  one gate, with an optional checkbox to backfill the N existing matches.
- **Three creation paths, one confirm**: chat (`RULE_HINT` → 4B model drafts
  via `daemon/llm/rule_author.py`, validated then gated), the ⚑ Rule button on
  a message (prefills sender), and Settings → `[mail_rules]` → ＋ New rule.
  Edits/toggles/deletes of existing rules are ungated.
- **Suggest labels (✨)** runs the local model once per unlabeled message
  (per-message verdicts, not batched — the 2026-07-13 triage lesson), writes
  NOTHING; each suggestion is a chip the user taps to apply.
- **Unread is inbox-scoped everywhere** — labeled mail has left the inbox, so
  it no longer counts as unread anywhere the UI shows a count.
- **Chips are separate inboxes (2026-07-16, user decision)** — there is no
  "All" chip. The default **Inbox** chip = `INBOX` mail carrying **no user
  label** (so mail labeled upstream in Gmail without being archived still
  leaves the default view); Unread applies the same exclusion; each label chip
  shows only its own mail. All chips re-query the local DB (`list_page`); the
  store-level `"all"` filter still exists for internal callers.

## HTML bodies (built 2026-07-16)
- `emails.body_html` stores the raw `text/html` part alongside the stripped
  plain `body` (which search/LLM paths keep using). `''` = message has no HTML
  part; `NULL` = row predates the column (hand-written additive migration in
  `db.py`) and gets a **lazy backfill**: `emails.get` fetches that one message
  (read scope, `format=full`) on first open and caches it forever. Failure
  leaves `NULL` so a later open retries.
- **List/search IPC responses never carry `body_html`** (`_LIST_COLS`) — 50
  raw HTML bodies per page is dead weight; the UI pulls one message's HTML via
  `emails.get` when it's opened (and re-uses it until the row is replaced).
- Rendering is `QTextBrowser` (`widgets.HtmlBody`), not WebEngine — no
  Chromium process on an iGPU/power budget. It cannot run scripts and never
  fetches remote resources (privacy: no tracking-pixel hits); links open in
  the system browser; body sits on a white card since HTML mail assumes a
  light background; widget height follows the document so the reading pane's
  outer scroll does all the scrolling.
